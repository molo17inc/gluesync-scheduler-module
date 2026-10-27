import { jobSpec } from '../lib/chronos.mjs';
import { corehub } from '../lib/stack.mjs';
import { request, sleep } from '../lib/util.mjs';
import { pipelineState, ranFailed, ranOk } from './common.mjs';

export default {
  name: 'corehub-faults',
  description: 'Core Hub misbehaving while Chronos is connected: 5xx, dropped connections, slow answers, rejected introspection, 401 on the module token',
  async run(ctx) {
    const { inst, api } = await ctx.resetMain();
    ctx.usedMain = true;
    const job = (await api.createJob(jobSpec('pipeline_stop', { name: 'fault probe', pipeline_id: 'pl-orders' }))).json;
    const attempt = async () => {
      const mark = await ctx.mark();
      const r = await api.runJob(job.id);
      return { r, calls: await ctx.calls(mark, { tag: 'chronos', only: /^POST/ }), after: (await api.getJob(job.id)).json };
    };

    const base = await attempt();
    ctx.check('baseline run succeeds', ranOk(base.r), `HTTP ${base.r.status}`);

    // ---- 503 once
    await corehub.addFault({ mode: 'status', status: 503, count: 1, match: { path: '/pipelines/pl-orders/commands' } });
    const f1 = await attempt();
    ctx.check('a 503 from Core Hub fails the run and the error is recorded on the job', ranFailed(f1.r) && !!f1.after.last_error_message && !!f1.after.last_run_error_time, `HTTP ${f1.r.status} err=${f1.after.last_error_message}`);
    ctx.check('a transient 503 does not lose the run (the command is retried)', f1.calls.length > 1 || ranOk(f1.r), `${f1.calls.length} POST(s) sent, run ${ranOk(f1.r) ? 'succeeded' : 'failed'}`, {
      finding: {
        id: 'no-retry-on-corehub-5xx',
        title: 'A single transient Core Hub 5xx fails a scheduled command for good',
        severity: 'observation',
        detail: 'Pipeline commands are sent once with no retry. A 503 at 01:50 means the "enter maintenance" run is lost until the next cron match (one week later for a weekly job); only Visualize refresh has a retry policy.',
        evidence: { posts: ctx.describe(f1.calls), lastError: f1.after.last_error_message },
      },
    });
    const f1b = await attempt();
    ctx.check('the next run succeeds and clears the error', ranOk(f1b.r) && !f1b.after.last_error_message, `HTTP ${f1b.r.status} err=${f1b.after.last_error_message}`);

    // ---- connection dropped mid-request
    await corehub.addFault({ mode: 'drop', count: 1, match: { path: '/pipelines/pl-orders/commands' } });
    const f2 = await attempt();
    ctx.check('a dropped connection fails the run with a recorded error', ranFailed(f2.r) && !!f2.after.last_error_message, `HTTP ${f2.r.status} err=${f2.after.last_error_message}`);
    ctx.check('the run after a dropped connection succeeds', (await attempt()).r.status === 200, '');

    // ---- slow answer, still inside every timeout
    await corehub.addFault({ mode: 'slow', delayMs: 9000, count: 1, match: { path: '/pipelines/pl-orders/commands' } });
    const probes = [];
    const slow = attempt();
    for (let i = 0; i < 3; i += 1) {
      await new Promise(r => setTimeout(r, 2500));
      probes.push((await request(api.url('/'), { timeoutMs: 3000 })).status);
    }
    const f3 = await slow;
    ctx.check('a 9 s answer still succeeds', ranOk(f3.r), `HTTP ${f3.r.status}`);
    ctx.check('the REST API keeps answering while a command waits on Core Hub', probes.every(s => s === 307), `probe statuses ${probes.join(',')}`);

    // ---- introspection (UI calls) while Core Hub /authentication/me is failing
    await corehub.addFault({ mode: 'status', status: 500, count: 3, match: { path: '/authentication/me' } });
    const fresh = await corehub.login('admin');
    const noCache = await request(api.url('/api/jobs/'), { headers: { cookie: `gs-auth=${fresh}` }, timeoutMs: 15000 });
    ctx.check('with introspection failing, an unknown session gets 401 (fail closed)', noCache.status === 401, `HTTP ${noCache.status} ${noCache.text?.slice(0, 120)}`);
    await corehub.clearFaults();
    const recovered = await request(api.url('/api/jobs/'), { headers: { cookie: `gs-auth=${fresh}` }, timeoutMs: 15000 });
    ctx.check('introspection recovers as soon as Core Hub does', recovered.status === 200, `HTTP ${recovered.status}`);
    const cached = await request(api.url('/api/jobs/'), { headers: { cookie: `gs-auth=${fresh}` }, timeoutMs: 15000 });
    await corehub.addFault({ mode: 'status', status: 500, count: 5, match: { path: '/authentication/me' } });
    const during = await request(api.url('/api/jobs/'), { headers: { cookie: `gs-auth=${fresh}` }, timeoutMs: 15000 });
    ctx.check('a session verified in the last 30 s keeps working during an introspection outage (cache)', cached.status === 200 && during.status === 200, `HTTP ${during.status}`);
    await corehub.clearFaults();

    // ---- module token rejected: Chronos must re-login and retry once
    const handshakesBefore = (await corehub.state()).stats.handshakes;
    await corehub.post('/__sandbox/revoke-tokens', { tag: 'chronos' });
    const f4 = await attempt();
    const handshakesAfter = (await corehub.state()).stats.handshakes;
    ctx.check('a revoked module token is recovered by re-login: the same run succeeds', ranOk(f4.r), `HTTP ${f4.r.status} err=${f4.after.last_error_message}`);
    ctx.check('Chronos opened a new WebSocket handshake to get a fresh token', handshakesAfter > handshakesBefore, `handshakes ${handshakesBefore} -> ${handshakesAfter}`);
    await sleep(15000);
    const mods = (await corehub.state()).modules.filter(m => m.tag === 'chronos' && m.open);
    const freshest = Math.max(0, ...mods.map(m => (m.lastPingAt ? Date.parse(m.lastPingAt) : 0)));
    const pingAge = freshest ? Date.now() - freshest : null;
    ctx.check('after the 401 recovery the Core Hub WebSocket is alive (pings keep arriving)', pingAge !== null && pingAge < 12000, `${mods.length} open module connection(s) without a ping: ${mods.map(m => `${m.id} pings=${m.pings}`).join(', ')}; newest ping ${pingAge === null ? 'never' : `${Math.round(pingAge / 1000)} s ago`}`, {
      finding: {
        id: 'sdk-relogin-on-throwaway-event-loop',
        title: 'The 401 recovery path reconnects the SDK on a throw-away event loop: the WebSocket dies right after the retried call',
        severity: 'defect',
        detail: 'CoreHubClient._reinitialize_sdk_client runs asyncio.run(initialize(force_reconnect=True)) when called from the worker thread, so the new WebSocket and its ping task belong to a loop that is closed as soon as the call returns. Core Hub then sees Chronos disconnected.',
        evidence: { modules: mods.map(m => ({ conn: m.id, lastPingAt: m.lastPingAt })), pingAgeMs: pingAge },
      },
    });
    const model = await pipelineState('pl-orders');
    ctx.note(`after the 401 retry the Core Hub model shows ${model.entities.filter(e => e.active).length}/4 entities active (stop was applied)`);
    await ctx.restartInstance(inst, { graceSec: 10 });
  },
};
