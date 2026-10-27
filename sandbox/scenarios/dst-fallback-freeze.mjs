import { corehub, fakeNow } from '../lib/stack.mjs';
import { at, commandTimes, fmtRome, harmlessJob, iso, lastPingFake, mustCreate, ROME, romeWall, spyTop, pySpyUnsupported, stackMentionsScheduler, watch } from './dst-lib.mjs';
import { jobSpec } from '../lib/chronos.mjs';
import { utc } from '../lib/util.mjs';

// 2026-10-25, EU fall-back: 03:00 CEST (01:00Z) -> 02:00 CET. The clock starts at
// 02:55:30 CEST (00:55:30Z) and the every-minute job reaches 02:59 CEST at 00:59:00Z.
const START = '2026-10-25T00:55:30Z';
const FREEZE_AT = at('2026-10-25T00:59:00Z');
const RESUME_AFTER = at('2026-10-25T01:01:00Z');

export default {
  name: 'dst-fallback-freeze',
  description: 'GSSD-1457: every-minute job plus the customer maintenance jobs across the EU fall-back (TZ Europe/Rome, clock 02:55:30 CEST)',
  async run(ctx) {
    const inst = await ctx.startInstance({ label: 'dst-freeze', tz: ROME, clockUtc: START });
    const api = ctx.api(inst);
    ctx.note(`image ${inst.imageTag}; fake clock ${utc(fakeNow(inst))} = ${romeWall(fakeNow(inst))} at readiness`);
    if (fakeNow(inst) > FREEZE_AT - 45000) {
      ctx.check('setup: clock was set early enough', false, `Chronos only became ready at ${utc(fakeNow(inst))}, too close to 00:59:00Z`);
      return;
    }

    await api.put('set timezone', '/api/settings/timezone', { value: ROME });
    const freq = await mustCreate(api, harmlessJob('every minute', '* * * * *', 'auto-dst-freeze'));
    const maint = [];
    for (const p of ['pl-maint-a', 'pl-maint-b']) {
      maint.push(await mustCreate(api, jobSpec('pipeline_enter_maintenance', { name: `enter maintenance ${p}`, cron_expression: '50 1 * * 0', pipeline_id: p })));
      maint.push(await mustCreate(api, jobSpec('pipeline_exit_maintenance', { name: `exit maintenance ${p}`, cron_expression: '10 3 * * 0', pipeline_id: p })));
    }
    ctx.note(`jobs created at ${romeWall(fakeNow(inst))}: every-minute id ${freq.id}, maintenance ids ${maint.map(m => m.id).join(',')}`);

    const created = (await api.getJob(freq.id)).json;
    const createdAtFake = at(created.created_at);
    const expectedNext = Math.ceil((createdAtFake + 1) / 60000) * 60000;
    const apiNext = new Date(created.next_run).getTime();
    ctx.check(
      'new every-minute job reports the next minute as next_run',
      Math.abs(apiNext - expectedNext) < 90000,
      `created ${iso(createdAtFake)}, next minute is ${iso(expectedNext)}, API next_run ${created.next_run} (= ${iso(apiNext)})`,
      {
        finding: {
          id: 'croniter-next-run-off-by-dst',
          title: 'Job next_run is computed wrongly inside the DST transition window',
          severity: 'defect',
          detail: 'JobService._calculate_next_run_times (croniter 2.0.1 on a pytz-aware "now") returns a next_run two hours late for a job created at 02:55 CEST on the fall-back night; the UI shows that value.',
          evidence: { created: iso(createdAtFake), expectedNext: iso(expectedNext), apiNextRun: created.next_run, timezone: created.timezone_name },
        },
      },
    );
    const result = await watch(ctx, inst, { untilMs: RESUME_AFTER + 60000, label: 'freeze' });
    const runs = commandTimes(result.calls, 'auto-dst-freeze', 'sync/stop');
    const lastRun = runs.length ? Math.max(...runs) : null;
    ctx.save('every-minute-runs.json', runs.map(t => ({ fake: iso(t), rome: romeWall(t) })));
    ctx.note(`every-minute job ran ${runs.length} time(s); last run ${lastRun ? fmtRome(lastRun) : 'never'}`);
    if (result.frozen) {
      ctx.note(`API stopped answering after ${result.lastOkFake ? fmtRome(result.lastOkFake) : '?'}; first failed probe ${fmtRome(result.firstFailFake)}`);
      ctx.save('py-spy-dump.txt', result.evidence.pySpy);
      ctx.save('py-spy-dump-later.txt', result.evidence.pySpyAfter);
      ctx.save('docker-stats.json', { atFreeze: result.evidence.stats, later: result.evidence.statsAfter, container: result.evidence.containerState });
    }

    const evidence = {
      lastApiAnswerFake: result.lastOkFake && `${iso(result.lastOkFake)} = ${romeWall(result.lastOkFake)}`,
      firstFailedProbeFake: result.firstFailFake && `${iso(result.firstFailFake)} = ${romeWall(result.firstFailFake)}`,
      lastEveryMinuteRun: lastRun && `${iso(lastRun)} = ${romeWall(lastRun)}`,
      lastWsPingFake: lastPingFake(result.pings) && `${iso(lastPingFake(result.pings))} = ${romeWall(lastPingFake(result.pings))}`,
      dockerStats: result.evidence.stats && { cpu: result.evidence.stats.CPUPerc, mem: result.evidence.stats.MemUsage },
      pySpyTopFrames: spyTop(result.evidence.pySpy),
    };
    const finding = {
      id: 'GSSD-1457-apscheduler-dst-freeze',
      title: 'Chronos freezes at 02:59 CEST on the EU fall-back night (APScheduler cron next-fire loop)',
      severity: 'defect',
      detail:
        'A cron job whose next fire time falls into the repeated 02:00-02:59 hour makes APScheduler (CronTrigger.get_next_fire_time, called from Job._get_run_times on the asyncio event loop) spin forever. The whole process stops: REST API, Core Hub WebSocket pings and every other job, with no error in the logs. A container restart is the only recovery.',
      evidence,
    };
    ctx.check('API keeps answering through the fall-back', !result.frozen, result.frozen ? `no HTTP answer after ${romeWall(result.lastOkFake)}` : 'answered until the end of the window', { finding, evidence });
    ctx.check('every-minute job keeps running after 02:59 CEST', lastRun !== null && lastRun >= RESUME_AFTER, `last run ${lastRun ? romeWall(lastRun) : 'none'}`, { finding, evidence });
    const lastPing = lastPingFake(result.pings);
    ctx.check('Core Hub WebSocket pings continue past 01:00Z', !!lastPing && lastPing >= RESUME_AFTER, `last ping ${lastPing ? romeWall(lastPing) : 'none'}`, { finding, evidence });
    if (result.frozen) {
      if (pySpyUnsupported(result.evidence.pySpy)) ctx.note(`py-spy cannot read this image: ${String(result.evidence.pySpy).trim()}`);
      else ctx.check('freeze is the APScheduler next-fire loop', stackMentionsScheduler(result.evidence.pySpy), spyTop(result.evidence.pySpy) || 'no py-spy output');
      const cpu = parseFloat(String(result.evidence.stats?.CPUPerc || '0'));
      ctx.check('frozen process burns a full CPU (busy loop, not a deadlock)', cpu > 80, `docker stats CPU ${result.evidence.stats?.CPUPerc}`);
    }
    const state = await corehub.state();
    ctx.note(`Core Hub model after the window: maintenance a=${state.pipelines.find(p => p.id === 'pl-maint-a')?.maintenance}, b=${state.pipelines.find(p => p.id === 'pl-maint-b')?.maintenance}`);
    ctx.save('summary.json', { ...evidence, frozen: result.frozen, runs: runs.length });
  },
};
