import { corehub } from '../lib/stack.mjs';
import { sleep } from '../lib/util.mjs';
import { waitCalls } from './common.mjs';

const chronosHooks = async () => (await corehub.state()).webhooks.chronos || [];
const posts = /^POST \/pipelines/;

export default {
  name: 'triggers',
  description: 'Trigger flows: CRUD, fire by secret token, fire from the UI, token rotation, platform-event flows with origin/broadcast routing, execution logs',
  async run(ctx) {
    const { api } = await ctx.resetMain();
    ctx.usedMain = true;
    const anon = (label, p, body, headers) => api.post(label, p, body, { anonymous: true, headers });

    // ---- CRUD
    const c = await api.post('create flow', '/api/triggers/', {
      name: 'sandbox flow',
      description: 'stop then start pl-extra',
      events: [
        { task_type: 'pipeline_stop', pipeline_id: 'pl-extra', execution_mode: 'sync' },
        { task_type: 'pipeline_start', pipeline_id: 'pl-extra', execution_mode: 'async' },
      ],
    });
    const flow = c.json || {};
    ctx.check('create returns 201 with the one-time secret token and a fire URL', c.status === 201 && !!flow.secret_token && /\/api\/triggers\/\d+\/fire$/.test(flow.trigger_url || ''), `HTTP ${c.status} url=${flow.trigger_url}`);
    ctx.check('create keeps the events in order', flow.events?.length === 2 && flow.events[0].task_type === 'pipeline_stop' && flow.events[1].position === 1, JSON.stringify(flow.events?.map(e => [e.position, e.task_type, e.execution_mode])));
    const list = await api.get('list flows', '/api/triggers/');
    ctx.check('list shows the flow without the secret token', list.status === 200 && list.json?.total === 1 && !('secret_token' in (list.json?.items?.[0] || {})), `HTTP ${list.status}`);
    const one = await api.get('get flow', `/api/triggers/${flow.id}`);
    ctx.check('get returns the flow', one.status === 200 && one.json?.id === flow.id, `HTTP ${one.status}`);
    ctx.check('get unknown flow is 404', (await api.get('get unknown flow', '/api/triggers/999')).status === 404, '');
    const viewer = await api.post('viewer create flow', '/api/triggers/', { name: 'v', events: [{ task_type: 'pipeline_stop', pipeline_id: 'pl-extra' }] }, { user: 'viewer' });
    ctx.check('viewer cannot create a flow (403)', viewer.status === 403, `HTTP ${viewer.status}`);
    const noEvents = await api.post('flow without events', '/api/triggers/', { name: 'empty', events: [] });
    ctx.check('a flow needs at least one event (422)', noEvents.status === 422, `HTTP ${noEvents.status}`);

    // ---- fire by secret token
    const noTok = await anon('fire without token', `/api/triggers/${flow.id}/fire`, {}, {});
    ctx.check('fire without a token is 401', noTok.status === 401, `HTTP ${noTok.status}`);
    const wrongTok = await anon('fire with wrong token', `/api/triggers/${flow.id}/fire`, {}, { 'X-Trigger-Token': 'nope' });
    ctx.check('fire with a wrong token is 401', wrongTok.status === 401, `HTTP ${wrongTok.status}`);
    let since = await ctx.mark();
    const fired = await anon('fire with token', `/api/triggers/${flow.id}/fire`, { source: 'ci' }, { 'X-Trigger-Token': flow.secret_token });
    ctx.check('fire with the token is queued (202)', fired.status === 202 && fired.json?.status === 'queued' && fired.json?.events_count === 2, `HTTP ${fired.status} ${JSON.stringify(fired.json)}`);
    let seen = await waitCalls(ctx, since, 'chronos', i => i.length >= 2, { timeoutMs: 30000, only: posts });
    ctx.expectCalls('fire runs the events in order', seen.items, ['POST /pipelines/pl-extra/commands/sync/stop', 'POST /pipelines/pl-extra/commands/sync/start']);
    since = await ctx.mark();
    const waited = await anon('fire with wait', `/api/triggers/${flow.id}/fire?wait=true`, {}, { 'X-Trigger-Token': flow.secret_token });
    ctx.check('fire with wait=true returns the final status (200 completed)', waited.status === 200 && waited.json?.status === 'completed', `HTTP ${waited.status} ${JSON.stringify(waited.json)}`);

    // ---- update, disable, token rotation, UI fire
    const upd = await api.put('update flow', `/api/triggers/${flow.id}`, { name: 'sandbox flow v2', events: [{ task_type: 'pipeline_stop', pipeline_id: 'pl-extra', execution_mode: 'async' }] });
    ctx.check('update replaces name and events', upd.status === 200 && upd.json?.name === 'sandbox flow v2' && upd.json?.events?.length === 1, `HTTP ${upd.status}`);
    const off = await api.patch('disable flow', `/api/triggers/${flow.id}/status`, { enabled: false });
    ctx.check('disable returns the flow with enabled=false', off.status === 200 && off.json?.enabled === false, `HTTP ${off.status}`);
    const offFire = await anon('fire disabled flow', `/api/triggers/${flow.id}/fire`, {}, { 'X-Trigger-Token': flow.secret_token });
    ctx.check('firing a disabled flow is 403', offFire.status === 403, `HTTP ${offFire.status}`);
    await api.patch('enable flow', `/api/triggers/${flow.id}/status`, { enabled: true });
    const rot = await api.post('regenerate token', `/api/triggers/${flow.id}/regenerate-token`, {});
    ctx.check('regenerate returns a new secret token', rot.status === 201 || rot.status === 200 ? !!rot.json?.secret_token && rot.json.secret_token !== flow.secret_token : false, `HTTP ${rot.status}`);
    const oldTok = await anon('fire with old token', `/api/triggers/${flow.id}/fire`, {}, { 'X-Trigger-Token': flow.secret_token });
    ctx.check('the old token is rejected after rotation (401)', oldTok.status === 401, `HTTP ${oldTok.status}`);
    const newTok = await anon('fire with new token', `/api/triggers/${flow.id}/fire?wait=true`, {}, { 'X-Trigger-Token': rot.json?.secret_token || '' });
    ctx.check('the new token works', newTok.status === 200, `HTTP ${newTok.status}`);
    const ui = await api.post('fire from UI', `/api/triggers/${flow.id}/fire-internal?wait=true`, {});
    ctx.check('admin can fire from the UI without a token', ui.status === 200, `HTTP ${ui.status}`);
    const uiViewer = await api.post('viewer fire from UI', `/api/triggers/${flow.id}/fire-internal`, {}, { user: 'viewer' });
    ctx.check('viewer cannot fire from the UI (403)', uiViewer.status === 403, `HTTP ${uiViewer.status}`);
    const logs = await api.get('execution logs', `/api/triggers/${flow.id}/logs`);
    ctx.check('execution logs list the fires with their source', logs.status === 200 && logs.json?.length >= 3 && logs.json.some(l => l.source === 'webhook') && logs.json.some(l => l.source === 'manual' || l.source === 'ui'), `HTTP ${logs.status} ${JSON.stringify((logs.json || []).map(l => [l.status, l.source]))}`);

    // ---- platform event flow (Core Hub calls Chronos back)
    const pf = await api.post('create platform flow', '/api/triggers/', {
      name: 'on cdc started',
      platform_event: 'ENTITY_CDC_STARTED',
      events: [{ task_type: 'pipeline_enter_maintenance', pipeline_id: 'pl-extra', execution_mode: 'async' }],
    });
    const hooks = await chronosHooks();
    const ph = hooks.find(h => h.id === `chronos-platform-${pf.json?.id}`);
    ctx.check('a platform-event flow registers a Core Hub webhook', pf.status === 201 && !!ph && ph.enabledEvents?.[0] === 'ENTITY_CDC_STARTED' && ph.webhookUrl === '{{chronos_address}}/api/webhooks/platform-event' && ph.customHeaders?.['X-Trigger-Flow-ID'] === String(pf.json?.id), JSON.stringify(ph));
    ctx.check('routing defaults to broadcast for a pipeline-level event', pf.json?.routing === 'broadcast', String(pf.json?.routing));
    since = await ctx.mark();
    await corehub.post('/__sandbox/emit', { event: 'ENTITY_CDC_STARTED', pipelineId: 'pl-orders', entityId: 'pl-orders-e1' });
    seen = await waitCalls(ctx, since, 'chronos', i => i.length >= 1, { timeoutMs: 30000, only: posts });
    ctx.expectCalls('a Core Hub platform event fires the flow', seen.items, ['POST /pipelines/pl-extra/commands/maintenance/enter']);
    const delivery = (await corehub.log({ since, kind: 'webhook' })).find(d => d.hook === `chronos-platform-${pf.json?.id}`);
    ctx.check('Core Hub delivered the platform event with X-Trigger-Flow-ID', delivery?.status === 200 && delivery.headers?.['X-Trigger-Flow-ID'] === String(pf.json?.id), JSON.stringify(delivery));
    await api.patch('disable platform flow', `/api/triggers/${pf.json?.id}/status`, { enabled: false });
    since = await ctx.mark();
    await corehub.post('/__sandbox/emit', { event: 'ENTITY_CDC_STARTED', pipelineId: 'pl-orders', entityId: 'pl-orders-e1' });
    await sleep(4000);
    ctx.check('a disabled platform flow ignores the event', (await ctx.calls(since, { tag: 'chronos', only: posts })).length === 0, '');
    await api.del('delete platform flow', `/api/triggers/${pf.json?.id}`);
    ctx.check('deleting the platform flow removes its webhook', (await chronosHooks()).every(h => h.id !== `chronos-platform-${pf.json?.id}`), JSON.stringify((await chronosHooks()).map(h => h.id)));

    // ---- origin routing: an entity-scoped event runs only the action aimed at its source
    const targets = [
      { task_type: 'entity_stop', pipeline_id: 'pl-orders', entity_ids: ['pl-orders-e1'], execution_mode: 'async' },
      { task_type: 'entity_stop', pipeline_id: 'pl-orders', entity_ids: ['pl-orders-e2'], execution_mode: 'async' },
    ];
    const origin = await api.post('create origin flow', '/api/triggers/', { name: 'on truncate (origin)', platform_event: 'TABLE_TRUNCATED', events: targets });
    ctx.check('routing defaults to origin for an entity-scoped event', origin.json?.routing === 'origin', String(origin.json?.routing));
    since = await ctx.mark();
    await corehub.post('/__sandbox/emit', { event: 'TABLE_TRUNCATED', pipelineId: 'pl-orders', entityId: 'pl-orders-e2' });
    seen = await waitCalls(ctx, since, 'chronos', i => i.length >= 1, { timeoutMs: 30000, only: posts });
    await sleep(2500);
    const originCalls = await ctx.calls(since, { tag: 'chronos', only: posts });
    ctx.expectCalls('origin routing runs only the action for the event source', originCalls, ['POST /pipelines/pl-orders/commands/sync/stop entity=pl-orders-e2']);
    const broadcast = await api.post('create broadcast flow', '/api/triggers/', { name: 'on truncate (broadcast)', platform_event: 'TABLE_TRUNCATED', routing: 'broadcast', events: targets });
    await api.patch('disable origin flow', `/api/triggers/${origin.json?.id}/status`, { enabled: false });
    since = await ctx.mark();
    await corehub.post('/__sandbox/emit', { event: 'TABLE_TRUNCATED', pipelineId: 'pl-orders', entityId: 'pl-orders-e2' });
    seen = await waitCalls(ctx, since, 'chronos', i => i.length >= 2, { timeoutMs: 30000, only: posts });
    ctx.check('broadcast routing runs every action', seen.items.length === 2, `${seen.items.length} call(s): ${seen.items.map(i => i.query?.entity).join(',')}`);
    void broadcast;

    // ---- delete
    const del = await api.del('delete flow', `/api/triggers/${flow.id}`);
    ctx.check('delete returns 204 and the flow is gone', del.status === 204 && (await api.get('get deleted flow', `/api/triggers/${flow.id}`)).status === 404, `HTTP ${del.status}`);
  },
};
