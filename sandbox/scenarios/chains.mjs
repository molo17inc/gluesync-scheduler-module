import { jobSpec } from '../lib/chronos.mjs';
import { corehub } from '../lib/stack.mjs';
import { sleep } from '../lib/util.mjs';
import { ents, pipelineState, ranFailed, setEntity, waitCalls } from './common.mjs';

const chronosHooks = async () => (await corehub.state()).webhooks.chronos || [];

export default {
  name: 'chains',
  description: 'Chained events: async and sync (Core Hub webhook wait), webhook registration lifecycle, failure propagation, group events, timeout',
  async run(ctx) {
    const { api } = await ctx.resetMain();
    ctx.usedMain = true;
    const only = /commands\//;

    // ---- sync chain: stop pl-orders, then (after Core Hub reports the stop) start it, async maintenance on pl-extra
    const created = await api.createJob(
      jobSpec('pipeline_stop', {
        name: 'chain parent',
        pipeline_id: 'pl-orders',
        chained_events: [
          { task_type: 'pipeline_start', pipeline_id: 'pl-orders', execution_mode: 'sync', webhook_timeout_seconds: 60 },
          { task_type: 'pipeline_enter_maintenance', pipeline_id: 'pl-extra', execution_mode: 'async' },
        ],
      }),
    );
    ctx.check('job with chained events is created (201)', created.status === 201 && created.json?.chained_events?.length === 2, `HTTP ${created.status} ${JSON.stringify(created.json?.chained_events?.map(e => [e.position, e.task_type, e.execution_mode]))}`);
    const parent = created.json;
    const syncEvent = parent?.chained_events?.[0];
    let hooks = await chronosHooks();
    ctx.check('a persistent Core Hub webhook is registered for the sync event', hooks.length === 1 && hooks[0].id === `chronos-sync-${syncEvent?.id}`, JSON.stringify(hooks.map(h => h.id)));
    const hook = hooks[0] || {};
    ctx.check('the webhook listens for the parent step (ENTITY_CDC_STOPPED) on the right pipeline', JSON.stringify(hook.enabledEvents) === JSON.stringify(['ENTITY_CDC_STOPPED']) && JSON.stringify(hook.pipelineFilter) === JSON.stringify(['pl-orders']), JSON.stringify({ e: hook.enabledEvents, p: hook.pipelineFilter }));
    ctx.check('the webhook calls back through the {{chronos_address}} template with EXT_MODULE and X-Event-ID', hook.webhookUrl === '{{chronos_address}}/api/webhooks/notify' && hook.customHeaders?.EXT_MODULE === 'chronos' && hook.customHeaders?.['X-Event-ID'] === String(syncEvent?.id), JSON.stringify({ url: hook.webhookUrl, h: hook.customHeaders }));

    const since = await ctx.mark();
    const run = await api.runJob(parent.id);
    ctx.check('parent run succeeds and reports the chain as started', run.status === 200 && run.json?.chain_started === true, `HTTP ${run.status} ${JSON.stringify(run.json)}`);
    const seq = await waitCalls(ctx, since, 'chronos', items => items.filter(i => i.method === 'POST' && only.test(i.path)).length >= 3, { timeoutMs: 25000, only: /^POST \/pipelines/ });
    const chainPosts = seq.items.filter(i => i.method === 'POST' && only.test(i.path));
    ctx.expectCalls('chain order: parent stop, then the sync start after the Core Hub callback', chainPosts.filter(i => i.path.startsWith('/pipelines/pl-orders/')), ['POST /pipelines/pl-orders/commands/sync/stop', 'POST /pipelines/pl-orders/commands/sync/start']);
    const asyncCalls = chainPosts.filter(i => i.path.startsWith('/pipelines/pl-extra/'));
    ctx.check('the trailing async chained event (enter maintenance on pl-extra) is executed', asyncCalls.length === 1, `${asyncCalls.length} call(s) to pl-extra`, {
      finding: {
        id: 'trailing-async-chained-event-dropped',
        title: 'An async chained event placed after the last awaited step never runs',
        severity: 'defect',
        detail: 'ChainExecutionService starts async events with asyncio.create_task() and does not await them. JobService runs the chain with asyncio.run() on a worker thread, which cancels pending tasks as soon as execute_chain returns, so an async event with nothing awaited after it is cancelled before it sends its request.',
        evidence: { chainCalls: ctx.describe(chainPosts) },
      },
    });
    const deliveries = (await corehub.log({ since, kind: 'webhook' })).filter(e => e.hook === `chronos-sync-${syncEvent?.id}`);
    ctx.check('Core Hub delivered the callback to Chronos (HTTP 200, one acknowledged)', deliveries.length >= 1 && deliveries.every(d => d.status === 200) && deliveries.some(d => String(d.res).includes('"acknowledged":true')), `${deliveries.length} deliveries: ${deliveries.map(d => `${d.status} ${d.res}`).join(' | ')}`);
    const status = await api.get('chain status', `/api/jobs/${parent.id}/chain-status`);
    ctx.check('chain-status reports chained events and no error', status.status === 200 && status.json?.has_chained_events === true && !status.json?.chain_error, JSON.stringify(status.json));

    // ---- update: replacing the chain re-registers webhooks; clearing it removes them
    const upd = await api.put('replace chain', `/api/jobs/${parent.id}`, { chained_events: [{ task_type: 'pipeline_start', pipeline_id: 'pl-orders', execution_mode: 'sync' }] });
    hooks = await chronosHooks();
    ctx.check('replacing the chained events swaps the registered webhook', upd.status === 200 && hooks.length === 1 && hooks[0].id === `chronos-sync-${upd.json?.chained_events?.[0]?.id}`, `HTTP ${upd.status} hooks ${JSON.stringify(hooks.map(h => h.id))}`);
    const clear = await api.put('clear chain', `/api/jobs/${parent.id}`, { chained_events: [] });
    hooks = await chronosHooks();
    ctx.check('clearing the chained events removes the webhook from Core Hub', clear.status === 200 && hooks.length === 0, `HTTP ${clear.status} hooks ${JSON.stringify(hooks.map(h => h.id))}`);

    // ---- delete removes webhooks
    const again = await api.createJob(jobSpec('pipeline_stop', { name: 'chain to delete', pipeline_id: 'pl-orders', chained_events: [{ task_type: 'pipeline_start', pipeline_id: 'pl-orders', execution_mode: 'sync' }] }));
    ctx.check('a second sync chain registers its webhook', (await chronosHooks()).length === 1, '');
    await api.deleteJob(again.json?.id);
    ctx.check('deleting the job removes its webhooks from Core Hub', (await chronosHooks()).length === 0, JSON.stringify((await chronosHooks()).map(h => h.id)));

    // ---- parent failure stops the chain
    await corehub.setMaintenance('pl-maint-b', true);
    const failing = await api.createJob(jobSpec('pipeline_start', { name: 'chain parent fails', pipeline_id: 'pl-maint-b', chained_events: [{ task_type: 'pipeline_stop', pipeline_id: 'pl-orders', execution_mode: 'async' }] }));
    const mark2 = await ctx.mark();
    const failRun = await api.runJob(failing.json?.id);
    await sleep(3000);
    const after = await ctx.calls(mark2, { tag: 'chronos', only: /^POST/ });
    ctx.check('a failing parent does not start its chain', ranFailed(failRun) && !after.some(c => c.path === '/pipelines/pl-orders/commands/sync/stop'), `HTTP ${failRun.status}; POSTs: ${after.map(c => c.path).join(', ')}`);
    await corehub.setMaintenance('pl-maint-b', false);

    // ---- sync event times out when Core Hub never calls back
    await corehub.post('/__sandbox/webhooks', { enabled: false });
    const slow = await api.createJob(jobSpec('pipeline_stop', { name: 'chain timeout', pipeline_id: 'pl-orders', chained_events: [{ task_type: 'pipeline_start', pipeline_id: 'pl-orders', execution_mode: 'sync', webhook_timeout_seconds: 6 }] }));
    const mark3 = await ctx.mark();
    await api.runJob(slow.json?.id);
    await sleep(12000);
    const cs = (await api.get('chain status after timeout', `/api/jobs/${slow.json?.id}/chain-status`)).json;
    const started = await ctx.calls(mark3, { tag: 'chronos', only: /sync\/start/ });
    ctx.check('a sync event whose callback never arrives times out and reports the error', !!cs?.chain_error && /timed out|failed/i.test(cs.chain_error) && started.length === 0, `chain_error=${cs?.chain_error}; start calls ${started.length}`);
    await corehub.post('/__sandbox/webhooks', { enabled: true });

    // ---- group_start / group_stop chained events: the internal /play and /pause routes ignore group_ids
    for (const p of ['pl-inventory']) for (const e of ents(p, 1, 2, 3, 4)) await setEntity(p, e, { active: false });
    const grp = await api.createJob(jobSpec('pipeline_stop', { name: 'chain with group start', pipeline_id: 'pl-orders', chained_events: [{ task_type: 'group_start', pipeline_id: 'pl-inventory', group_ids: ['grp-b'], execution_mode: 'async' }] }));
    const mark4 = await ctx.mark();
    await api.runJob(grp.json?.id);
    const g = await waitCalls(ctx, mark4, 'chronos', items => items.some(i => /pl-inventory\/commands\/sync\/start/.test(i.path)), { timeoutMs: 30000, only: /^POST/ });
    await sleep(1500);
    const inv = await pipelineState('pl-inventory');
    const activeAfter = inv.entities.filter(e => e.active).map(e => e.id);
    const groupEvidence = { chainedEvent: 'group_start pl-inventory grp-b', coreHubCalls: ctx.describe(g.items.filter(i => i.path.includes('pl-inventory'))), entitiesActiveAfter: activeAfter, expected: ents('pl-inventory', 3, 4) };
    ctx.check('a group_start chained event starts only the entities of that group', JSON.stringify([...activeAfter].sort()) === JSON.stringify(ents('pl-inventory', 3, 4)), `active after: ${activeAfter.join(', ') || 'none'}`, {
      finding: {
        id: 'group-chained-events-act-on-whole-pipeline',
        title: 'group_start / group_stop in chained events and trigger flows start or stop the whole pipeline',
        severity: 'defect',
        detail: 'Scheduled group jobs call start-group/stop-group per group, but chained events and trigger flows map GROUP_START/GROUP_STOP to the internal /play and /pause routes, which read only entity_ids. The group_ids in the payload are ignored, so every entity in the pipeline is started or stopped (PipelineManager.play_groups/pause_groups exist but no route calls them).',
        evidence: groupEvidence,
      },
      evidence: groupEvidence,
    });
  },
};
