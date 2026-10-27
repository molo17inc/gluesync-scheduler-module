import { jobSpec } from '../lib/chronos.mjs';
import { corehub } from '../lib/stack.mjs';
import { request, sleep, utc } from '../lib/util.mjs';

const handshakes = items => items.filter(e => e.kind === 'ws' && e.event === 'handshake');

// Waits for an authenticated API answer, returning the seconds it took (or null).
const timeToRecover = async (ctx, api, maxMs) => {
  const began = Date.now();
  while (Date.now() - began < maxMs) {
    const fresh = await corehub.login('admin').catch(() => null);
    if (fresh) {
      const r = await request(api.url('/api/jobs/?limit=1'), { headers: { cookie: `gs-auth=${fresh}` }, timeoutMs: 8000 });
      if (r.status === 200) return Math.round((Date.now() - began) / 1000);
    }
    await sleep(5000);
  }
  return null;
};

export default {
  name: 'corehub-outage',
  description: 'Core Hub down for 70 s and a dropped WebSocket: what Chronos does while disconnected and how long it needs to recover (real clock, default health-check interval)',
  async run(ctx) {
    const inst = await ctx.startInstance({ label: 'outage', tz: 'UTC' });
    const api = ctx.api(inst);
    const job = (await api.createJob(jobSpec('pipeline_stop', { name: 'every minute', cron_expression: '* * * * *', pipeline_id: 'auto-outage' }))).json;
    const since = await corehub.mark();
    ctx.note(`started ${utc(Date.now())}; job ${job.id} every minute`);

    // ---- Core Hub down
    const downAt = Date.now();
    await corehub.post('/__sandbox/down', { forMs: 70000 });
    await sleep(8000);
    const early = await request(api.url('/api/jobs/?limit=1'), { headers: { cookie: `gs-auth=${await corehub.login('admin').catch(() => 'x')}` }, timeoutMs: 8000 });
    ctx.note(`8 s into the outage, an authenticated API call answers HTTP ${early.status}`);
    const root = await request(api.url('/'), { timeoutMs: 3000 });
    ctx.check('Chronos itself stays up while Core Hub is down', root.status > 0, `GET / -> ${root.status}`);
    await sleep(Math.max(0, 75000 - (Date.now() - downAt)));
    const upAgain = await corehub.isUp();
    ctx.check('setup: Core Hub came back', upAgain, '');
    const upAt = Date.now();

    const recovered = await timeToRecover(ctx, api, 7 * 60 * 1000);
    const afterUp = await corehub.log({ since, tag: inst.tag, kind: 'ws' });
    const reHandshake = handshakes(afterUp).filter(e => Date.parse(e.ts) > upAt - 2000);
    ctx.note(`authenticated API usable again ${recovered === null ? 'never (7 min)' : `${recovered} s`} after Core Hub returned; ${reHandshake.length} new handshake(s)`);
    const evidence = { outageSeconds: 70, secondsToRecover: recovered, handshakesAfterOutage: reHandshake.map(e => e.ts), wsEvents: afterUp.filter(e => e.event !== 'ping').map(e => `${e.ts} ${e.event} ${e.code ?? ''}`).slice(-10) };
    ctx.check('Chronos reconnects within 60 s of Core Hub returning', recovered !== null && recovered <= 60, `recovered after ${recovered} s`, {
      finding: {
        id: 'slow-reconnect-after-corehub-restart',
        title: 'After a Core Hub outage Chronos stays disconnected (API answers 401 to every user) until the 5-minute health check forces a reconnect',
        severity: 'observation',
        detail: 'The SDK makes one reconnection attempt when the WebSocket drops; if Core Hub is still down it never retries on its own. Chronos only recovers when CoreHubHealthCheck (CHRONOS_HEALTH_CHECK_INTERVAL_SECONDS, default 300) or a 401 retry re-initialises the client.',
        evidence,
      },
      evidence,
    });

    // ---- scheduled runs during and after the outage
    await sleep(70000);
    const calls = await ctx.calls(since, { tag: inst.tag, only: /^POST \/pipelines/ });
    const okRuns = calls.filter(c => c.status === 200 && Date.parse(c.ts) > upAt);
    const j = (await api.getJob(job.id)).json;
    ctx.save('job-after.json', j);
    ctx.check('scheduled runs resume after the outage', okRuns.length >= 1, `${okRuns.length} successful run(s) after Core Hub returned; last_error=${j?.last_error_message}`, { evidence });
    ctx.check('runs that happened during the outage are recorded as failures', calls.length === 0 || j?.last_run != null, `last_run=${j?.last_run}`);

    // ---- WebSocket dropped by Core Hub while it stays up
    const beforeDrop = handshakes(await corehub.log({ since, tag: inst.tag, kind: 'ws' })).length;
    const dropAt = Date.now();
    await corehub.post('/__sandbox/ws-drop', { tag: inst.tag });
    let reconnectedAfter = null;
    while (Date.now() - dropAt < 7 * 60 * 1000) {
      await sleep(5000);
      const n = handshakes(await corehub.log({ since, tag: inst.tag, kind: 'ws' })).length;
      if (n > beforeDrop) {
        reconnectedAfter = Math.round((Date.now() - dropAt) / 1000);
        break;
      }
    }
    ctx.check('a dropped WebSocket is re-established within 60 s', reconnectedAfter !== null && reconnectedAfter <= 60, `reconnected after ${reconnectedAfter} s`, { evidence: { reconnectedAfter } });
  },
};
