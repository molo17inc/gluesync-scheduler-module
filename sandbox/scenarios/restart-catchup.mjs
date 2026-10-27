import { containerLogs, corehub, fakeNow } from '../lib/stack.mjs';
import { sleep, utc } from '../lib/util.mjs';
import { at, commandTimes, harmlessJob, iso, mustCreate } from './dst-lib.mjs';

// Outside DST: a daily job fires at 12:30 UTC while Chronos is stopped, and Chronos
// is started again 10 minutes later (well inside the 3600 s misfire grace the jobs
// are created with). A scheduler with a persistent job store, or any catch-up step,
// runs the missed job once on startup.
const T0 = '2026-06-10T12:00:00Z';
const T1 = '2026-06-10T12:40:00Z';

export default {
  name: 'restart-catchup',
  description: 'A daily job misses its 12:30 fire time while Chronos is down; after the restart it should run (grace 3600 s) but jobs are re-created from "now"',
  async run(ctx) {
    let inst = await ctx.startInstance({ label: 'catchup', tz: 'UTC', clockUtc: T0 });
    let api = ctx.api(inst);
    ctx.note(`first start ready at ${utc(fakeNow(inst))}`);
    const job = await mustCreate(api, harmlessJob('daily 12:30 UTC', '30 12 * * *', 'auto-catchup'));
    const before = (await api.getJob(job.id)).json;
    ctx.note(`created at ${utc(fakeNow(inst))}, next_run ${before.next_run}`);
    ctx.check('setup: job scheduled for today 12:30 UTC', String(before.next_run).startsWith('2026-06-10T12:30'), String(before.next_run));
    if (fakeNow(inst) > at('2026-06-10T12:25:00Z')) {
      ctx.check('setup: first incarnation ended before the fire time', false, utc(fakeNow(inst)));
      return;
    }

    inst = await ctx.restartInstance(inst, { clockUtc: T1, graceSec: 5 });
    api = ctx.api(inst);
    ctx.note(`second start ready at ${utc(fakeNow(inst))}`);
    const since = await corehub.mark();
    await sleep(60000);
    const runs = commandTimes(await ctx.calls(since, { tag: inst.tag }), 'auto-catchup', 'sync/stop');
    const after = (await api.getJob(job.id)).json;
    const logs = await containerLogs(inst.name);
    const nextLogged = (logs.match(/Next scheduled run will be: (\S+ \S+ \S+)/) || [])[1];
    const evidence = {
      missedFireTime: '2026-06-10T12:30:00Z',
      restartedAt: iso(at(T1)),
      runsAfterRestart: runs.map(iso),
      lastRun: after.last_run,
      apiNextRun: after.next_run,
      apschedulerNextRunLogged: nextLogged,
    };
    ctx.check('missed run is executed after the restart (within the 3600 s grace)', runs.length === 1, `runs after restart: ${runs.length}`, {
      finding: {
        id: 'GSSD-1457-missed-runs-lost-on-restart',
        title: 'Runs missed while Chronos was down are never recovered: jobs are re-created from "now" at startup',
        severity: 'defect',
        detail: 'core/app.py _load_enabled_jobs re-creates every enabled job in a MemoryJobStore from the current time, so APScheduler never sees the missed fire time and misfire_grace_time never applies.',
        evidence,
      },
      evidence,
    });
    ctx.check('API next_run is not left in the past after the restart', !!after.next_run && new Date(after.next_run).getTime() > at(T1), `API next_run ${after.next_run} vs restart ${iso(at(T1))}`, { evidence });
    ctx.save('summary.json', evidence);
  },
};
