import { containerLogs, fakeNow } from '../lib/stack.mjs';
import { utc } from '../lib/util.mjs';
import { at, commandTimes, harmlessJob, iso, ROME, romeWall, setJobTimezone, spyTop, watch } from './dst-lib.mjs';

// 2026-03-29, EU spring-forward: 02:00 CET (01:00Z) becomes 03:00 CEST, so wall times
// 02:00-02:59 do not exist. The clock starts at 01:57:30 CET (00:57:30Z).
const START = '2026-03-29T00:57:00Z';
const END = '2026-03-29T01:03:30Z';

export default {
  name: 'dst-springforward',
  description: 'EU spring-forward 2026-03-29 with a "30 2 * * *" job (non-existent 02:30) and an every-minute job: skipped runs and hangs',
  async run(ctx) {
    const inst = await ctx.startInstance({ label: 'dst-spring', tz: ROME, clockUtc: START });
    const api = ctx.api(inst);
    ctx.note(`ready at ${utc(fakeNow(inst))} (${romeWall(fakeNow(inst))})`);
    await setJobTimezone(api, ROME);
    const daily = await api.createJob(harmlessJob('daily 02:30', '30 2 * * *', 'auto-spring-daily'));
    const every = await api.createJob(harmlessJob('every minute', '* * * * *', 'auto-spring-every'));
    const createdAt = fakeNow(inst);
    const next = {
      daily: (await api.getJob(daily.json.id)).json?.next_run,
      every: (await api.getJob(every.json.id)).json?.next_run,
    };
    const logs = await containerLogs(inst.name);
    const logged = [...logs.matchAll(/Next scheduled run will be: (\S+ \S+ \S+) which is a (\w+)/g)].map(m => `${m[1]} (${m[2]})`);
    ctx.note(`jobs created at ${romeWall(createdAt)}; API next_run daily=${next.daily} every-minute=${next.every}; APScheduler next runs logged: ${logged.join(' | ')}`);

    const result = await watch(ctx, inst, { untilMs: at(END), label: 'spring' });
    const everyRuns = commandTimes(result.calls, 'auto-spring-every', 'sync/stop');
    const dailyRuns = commandTimes(result.calls, 'auto-spring-daily', 'sync/stop');
    const gap = [];
    for (let i = 1; i < everyRuns.length; i += 1) if (everyRuns[i] - everyRuns[i - 1] > 90000) gap.push(`${romeWall(everyRuns[i - 1])} -> ${romeWall(everyRuns[i])}`);
    ctx.save('spring-runs.json', { every: everyRuns.map(t => ({ fake: iso(t), rome: romeWall(t) })), daily: dailyRuns.map(t => ({ fake: iso(t), rome: romeWall(t) })), nextRun: next, apschedulerNextRunLogged: logged });
    ctx.note(`every-minute runs: ${everyRuns.length}, first ${everyRuns[0] ? romeWall(everyRuns[0]) : '-'}, last ${everyRuns.length ? romeWall(Math.max(...everyRuns)) : '-'}; gaps over 90 s: ${gap.join(' | ') || 'none'}`);
    ctx.note(`daily "30 2 * * *" job runs inside the window: ${dailyRuns.length}; the logged next fire time for it is ${logged[0] || 'unknown'} (the 02:30 wall time does not exist on 2026-03-29)`);

    const evidence = { lastApiAnswer: result.lastOkFake && `${iso(result.lastOkFake)} = ${romeWall(result.lastOkFake)}`, pySpyTopFrames: spyTop(result.evidence.pySpy) };
    ctx.check('spring-forward: Chronos stays responsive', !result.frozen, result.frozen ? `silent after ${romeWall(result.lastOkFake)}` : 'answered through 03:03 CEST', {
      finding: { id: 'dst-springforward-freeze', title: 'Chronos freezes across the spring-forward', severity: 'defect', detail: 'The scheduler loop hangs when the clock jumps over 02:00-02:59.', evidence },
      evidence,
    });
    ctx.check('spring-forward: every-minute job keeps running after 03:00 CEST', everyRuns.some(t => t >= at('2026-03-29T01:01:00Z')), `last run ${everyRuns.length ? romeWall(Math.max(...everyRuns)) : 'none'}; API answered until ${romeWall(result.lastOkFake)}`, {
      finding: {
        id: 'springforward-jobs-paused-one-hour',
        title: 'After the spring-forward Chronos keeps answering but stops running jobs for about an hour',
        severity: 'defect',
        detail: 'After the 01:59 CET run APScheduler logs "next run at 03:00:00 CEST" (one minute later in real time), but nothing runs for the rest of the window. APScheduler computes its sleep as next_run_time - now with both values on the same ZoneInfo object, and Python subtracts those as naive wall-clock times (03:00 - 01:59 = 1 h 01 min instead of 1 min), so the scheduler sleeps an hour too long. Every job that falls in the hour after the gap is late.',
        evidence: { everyMinuteRuns: everyRuns.map(romeWall), apiAnsweredUntil: romeWall(result.lastOkFake), nextRunLogged: logged },
      },
    });
    ctx.check('spring-forward: no minute is run twice or skipped other than the gap hour', gap.length === 0, gap.join(' | ') || 'continuous');
  },
};
