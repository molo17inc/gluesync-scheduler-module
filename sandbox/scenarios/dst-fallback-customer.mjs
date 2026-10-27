import { jobSpec } from '../lib/chronos.mjs';
import { containerLogs, corehub, fakeNow } from '../lib/stack.mjs';
import { sleep, utc } from '../lib/util.mjs';
import { at, commandTimes, harmlessJob, iso, ROME, romeWall, spyTop, pySpyUnsupported, stackMentionsScheduler, watch } from './dst-lib.mjs';

// iperal, GSSD-1457: Chronos 0.2.2, TZ Europe/Rome, jobs created with timezone
// Europe/Rome. enter-maintenance 01:50 and exit-maintenance 03:10 on Sundays for two
// pipelines, plus an every-minute job.
//
// Real time cannot cover the customer's 70 minutes between 01:50 and 02:59, so the
// night is replayed in three container incarnations on the same data volume:
//   A  23:48:30Z (01:48:30 CEST)  the 01:50 enter jobs run
//   B  00:55:30Z (02:55:30 CEST)  the fall-back freeze
//   C  05:03:00Z (06:03:00 CET)   the restart the customer did, 03:10 exit jobs missed
const PHASE_A = '2026-10-24T23:48:30Z';
const PHASE_B = '2026-10-25T00:55:30Z';
const PHASE_C = '2026-10-25T05:03:00Z';

export default {
  name: 'dst-fallback-customer',
  description: 'GSSD-1457 replay: 01:50 enter / 03:10 exit maintenance jobs and an every-minute job, freeze at 02:59 CEST, restart at 06:03 CET',
  async run(ctx) {
    let inst = await ctx.startInstance({ label: 'dst-customer', tz: ROME, clockUtc: PHASE_A });
    let api = ctx.api(inst);
    ctx.note(`phase A ready at ${utc(fakeNow(inst))} (${romeWall(fakeNow(inst))})`);
    if (fakeNow(inst) > at('2026-10-24T23:49:40Z')) {
      ctx.check('setup: phase A clock early enough', false, `ready at ${utc(fakeNow(inst))}`);
      return;
    }

    await api.put('set timezone', '/api/settings/timezone', { value: ROME });
    const ids = {};
    for (const p of ['pl-maint-a', 'pl-maint-b']) {
      const enter = await api.createJob(jobSpec('pipeline_enter_maintenance', { name: `enter maintenance ${p}`, cron_expression: '50 1 * * 0', pipeline_id: p }));
      const exit = await api.createJob(jobSpec('pipeline_exit_maintenance', { name: `exit maintenance ${p}`, cron_expression: '10 3 * * 0', pipeline_id: p }));
      ids[`enter-${p}`] = enter.json?.id;
      ids[`exit-${p}`] = exit.json?.id;
    }
    const freq = await api.createJob(harmlessJob('frequent job', '* * * * *', 'auto-customer-freq'));
    ids.frequent = freq.json?.id;
    ctx.note(`jobs: ${JSON.stringify(ids)}; next_run of exit jobs at creation: ${JSON.stringify(await nextRuns(api, ids))}`);

    // ---- phase A: the 01:50 jobs run
    const sinceA = await corehub.mark();
    await watch(ctx, inst, { untilMs: at('2026-10-24T23:51:15Z'), label: 'phase-a', failsToFreeze: 99 });
    const enterCalls = await ctx.calls(sinceA, { tag: inst.tag, only: /maintenance\/enter/ });
    const enters = enterCalls.map(e => e.path).sort();
    ctx.check('phase A: 01:50 CEST enter-maintenance runs on both pipelines', enters.length === 2 && enters[0].includes('pl-maint-a') && enters[1].includes('pl-maint-b'), JSON.stringify(enterCalls.map(e => `${e.fakeTs} ${e.path}`)));
    const stateA = await corehub.state();
    ctx.check('phase A: both pipelines are in maintenance', stateA.pipelines.filter(p => p.id.startsWith('pl-maint')).every(p => p.maintenance), JSON.stringify(stateA.pipelines.filter(p => p.id.startsWith('pl-maint')).map(p => [p.id, p.maintenance])));
    await ctx.saveInstanceLogs(inst, '-phase-a');

    // ---- phase B: the freeze
    inst = await ctx.restartInstance(inst, { clockUtc: PHASE_B });
    api = ctx.api(inst);
    ctx.note(`phase B ready at ${utc(fakeNow(inst))} (${romeWall(fakeNow(inst))})`);
    if (fakeNow(inst) > at('2026-10-25T00:58:15Z')) {
      ctx.check('setup: phase B clock early enough', false, `ready at ${utc(fakeNow(inst))}`);
      return;
    }
    const sinceB = await corehub.mark();
    const b = await watch(ctx, inst, { untilMs: at('2026-10-25T01:04:00Z'), label: 'phase-b', postFreezeMs: 60000 });
    const runsB = commandTimes(b.calls, 'auto-customer-freq', 'sync/stop');
    const lastB = runsB.length ? Math.max(...runsB) : null;
    const evidenceB = {
      lastApiAnswer: b.lastOkFake && `${iso(b.lastOkFake)} = ${romeWall(b.lastOkFake)}`,
      lastFrequentRun: lastB && `${iso(lastB)} = ${romeWall(lastB)}`,
      docker: b.evidence.stats && { cpu: b.evidence.stats.CPUPerc },
      pySpyTopFrames: spyTop(b.evidence.pySpy),
    };
    const finding = {
      id: 'GSSD-1457-apscheduler-dst-freeze',
      title: 'Chronos freezes at 02:59 CEST on the EU fall-back night (APScheduler cron next-fire loop)',
      severity: 'defect',
      detail: 'Same freeze as dst-fallback-freeze, reproduced with the customer job set. Everything stops: API, Core Hub WebSocket, every scheduled job. No error is logged.',
      evidence: evidenceB,
    };
    ctx.check('phase B: Chronos keeps running through 02:59 CEST', !b.frozen, b.frozen ? `API silent after ${romeWall(b.lastOkFake)}` : 'API answered through the window', { finding, evidence: evidenceB });
    ctx.check('phase B: frequent job still runs after 02:59 CEST', lastB !== null && lastB >= at('2026-10-25T01:00:00Z'), `last run ${lastB ? romeWall(lastB) : 'none'}`, { finding, evidence: evidenceB });
    if (b.frozen) {
      ctx.save('phase-b-py-spy.txt', b.evidence.pySpy);
      if (pySpyUnsupported(b.evidence.pySpy)) ctx.note(`py-spy cannot read this image: ${String(b.evidence.pySpy).trim()}`);
      else ctx.check('phase B: freeze is the APScheduler next-fire loop', stackMentionsScheduler(b.evidence.pySpy), spyTop(b.evidence.pySpy));
    }
    const logsB = await ctx.saveInstanceLogs(inst, '-phase-b');
    ctx.check('phase B: nothing is logged at the moment of the freeze', !/error|exception|traceback/i.test(lastLines(logsB, 20)), lastLines(logsB, 6));

    // ---- phase C: the customer's restart at 06:03 CET
    inst = await ctx.restartInstance(inst, { clockUtc: PHASE_C, graceSec: 5 });
    api = ctx.api(inst);
    ctx.note(`phase C ready at ${utc(fakeNow(inst))} (${romeWall(fakeNow(inst))})`);
    const sinceC = await corehub.mark();
    await sleep(75000);
    const exits = await ctx.calls(sinceC, { tag: inst.tag, only: /maintenance\/exit/ });
    const stateC = await corehub.state();
    const stillIn = stateC.pipelines.filter(p => p.id.startsWith('pl-maint') && p.maintenance).map(p => p.id);
    const next = await nextRuns(api, ids);
    const logsC = await containerLogs(inst.name);
    const nextFromLog = nextScheduledFromLog(logsC, ids);
    ctx.save('phase-c.json', { exitCallsAfterRestart: exits.map(e => ({ at: e.fakeTs, path: e.path })), maintenance: stateC.pipelines.map(p => [p.id, p.maintenance]), apiNextRun: next, apschedulerNextRun: nextFromLog });
    const evC = { restartedAt: romeWall(fakeNow(inst)), exitCallsAfterRestart: exits.length, pipelinesStillInMaintenance: stillIn, apiNextRun: next, apschedulerNextRunFromLog: nextFromLog };
    const missed = {
      id: 'GSSD-1457-missed-runs-lost-on-restart',
      title: 'Runs missed while Chronos was down are never recovered: jobs are re-created from "now" at startup',
      severity: 'defect',
      detail:
        'Startup re-creates every enabled job in an in-memory job store from the current time (core/app.py _load_enabled_jobs), so a fire time that passed while the container was down or frozen is dropped without a trace. The 03:10 exit-maintenance jobs are rescheduled for the following Sunday and the pipelines stay in maintenance. Note the 06:03 restart is also beyond the 3600 s misfire_grace_time, so a catch-up needs both persistence and a larger grace (or an explicit recovery step).',
      evidence: evC,
    };
    ctx.check('phase C: missed 03:10 exit-maintenance jobs are recovered after the restart', exits.length >= 2, `exit calls after restart: ${exits.length}`, { finding: missed, evidence: evC });
    ctx.check('phase C: pipelines are out of maintenance after the restart', stillIn.length === 0, `still in maintenance: ${stillIn.join(', ') || 'none'}`, { finding: missed, evidence: evC });
    const nov1 = Object.entries(nextFromLog).filter(([k]) => k.startsWith('exit-'));
    ctx.check('phase C: exit jobs were rescheduled for the following Sunday (2026-11-01 03:10 CET)', nov1.length === 2 && nov1.every(([, v]) => v.startsWith('2026-11-01 03:10:00')), JSON.stringify(nextFromLog));
    ctx.note(`API next_run after restart (persisted column, not recomputed at startup): ${JSON.stringify(next)}`);
    ctx.note(`APScheduler next run logged at startup: ${JSON.stringify(nextFromLog)}`);
  },
};

const lastLines = (text, n) => text.split('\n').slice(-n).join('\n');

const nextRuns = async (api, ids) => {
  const out = {};
  for (const [k, id] of Object.entries(ids)) {
    const r = await api.getJob(id);
    out[k] = r.json?.next_run || null;
  }
  return out;
};

// "Next scheduled run will be: 2026-11-01 03:10:00 +0100 which is a sunday" follows
// "Using timezone Europe/Rome for job job_N" in the Chronos log.
const nextScheduledFromLog = (logs, ids) => {
  const out = {};
  const byJob = Object.fromEntries(Object.entries(ids).map(([k, id]) => [`job_${id}`, k]));
  let current = null;
  logs.split('\n').forEach(line => {
    const j = line.match(/Using timezone \S+ for job (job_\d+)/);
    if (j) current = j[1];
    const n = line.match(/Next scheduled run will be: (\S+ \S+ \S+)/);
    if (n && current && byJob[current]) out[byJob[current]] = n[1];
    const c = line.match(/Created scheduled job: (job_\d+)/);
    if (c) current = null;
  });
  return out;
};
