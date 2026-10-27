import { sleep } from '../lib/util.mjs';

const H = { EXT_MODULE: 'chronos' };

export default {
  name: 'webhooks',
  description: 'Core Hub callback endpoints (/api/webhooks/*) and the internal pipeline API: header validation and who can reach them without credentials',
  async run(ctx) {
    const { api } = await ctx.resetMain();
    ctx.usedMain = true;
    const anon = (label, p, body, headers) => api.post(label, p, body, { anonymous: true, headers });

    const n1 = await anon('notify without headers', '/api/webhooks/notify', {}, {});
    ctx.check('notify without EXT_MODULE is 400', n1.status === 400, `HTTP ${n1.status}`);
    const n2 = await anon('notify wrong module', '/api/webhooks/notify', {}, { EXT_MODULE: 'other', 'X-Event-ID': '1' });
    ctx.check('notify with another module is 400', n2.status === 400, `HTTP ${n2.status}`);
    const n3 = await anon('notify without event id', '/api/webhooks/notify', {}, H);
    ctx.check('notify without X-Event-ID is 400', n3.status === 400, `HTTP ${n3.status}`);
    const n4 = await anon('notify unknown event', '/api/webhooks/notify', { user: 'x' }, { ...H, 'X-Event-ID': '424242' });
    ctx.check('notify for an event with no active listener is acknowledged but ignored', n4.status === 200 && n4.json?.acknowledged === false, `HTTP ${n4.status} ${JSON.stringify(n4.json)}`);
    const p1 = await anon('platform-event without headers', '/api/webhooks/platform-event', {}, {});
    ctx.check('platform-event without EXT_MODULE is 400', p1.status === 400, `HTTP ${p1.status}`);
    const p2 = await anon('platform-event bad flow id', '/api/webhooks/platform-event', {}, { ...H, 'X-Trigger-Flow-ID': 'abc' });
    ctx.check('platform-event with a non-numeric flow id is 400', p2.status === 400, `HTTP ${p2.status}`);

    // A trigger flow that stops pl-extra. Its secret token protects /api/triggers/{id}/fire.
    const flow = await api.post('create trigger flow', '/api/triggers/', { name: 'webhook probe', events: [{ task_type: 'pipeline_stop', pipeline_id: 'pl-extra', execution_mode: 'async' }] });
    const id = flow.json?.id;
    const noToken = await anon('fire without token', `/api/triggers/${id}/fire`, {}, {});
    ctx.check('fire without the secret token is 401', noToken.status === 401, `HTTP ${noToken.status}`);
    const since = await ctx.mark();
    const viaCallback = await anon('platform-event callback for a real flow, no credentials', '/api/webhooks/platform-event', { event: { type: 'ENTITY_CDC_STARTED' } }, { ...H, 'X-Trigger-Flow-ID': String(id) });
    await sleep(4000);
    const fired = await ctx.calls(since, { tag: 'chronos', only: /commands\/sync\/stop/ });
    const evidence = { request: `POST /api/webhooks/platform-event  EXT_MODULE: chronos  X-Trigger-Flow-ID: ${id}  (no cookie, no token)`, status: viaCallback.status, coreHubCalls: ctx.describe(fired) };
    ctx.check('an anonymous caller cannot fire a trigger flow through /api/webhooks/platform-event', !(viaCallback.status === 200 && fired.length > 0), `HTTP ${viaCallback.status}, Core Hub saw ${fired.length} pipeline command(s)`, {
      finding: {
        id: 'platform-event-endpoint-unauthenticated',
        title: 'POST /api/webhooks/platform-event fires any enabled trigger flow for a caller that only sends two static headers',
        severity: 'defect',
        detail: 'The endpoint checks EXT_MODULE: chronos and X-Trigger-Flow-ID, but not the flow secret token (required by /api/triggers/{id}/fire) nor any Core Hub identity. Anyone who can reach Chronos can run the flow\'s pipeline actions by guessing the integer flow id.',
        evidence,
      },
      evidence,
    });
  },
};
