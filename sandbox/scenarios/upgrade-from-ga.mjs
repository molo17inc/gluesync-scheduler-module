import { jobSpec } from '../lib/chronos.mjs';
import { env } from '../lib/env.mjs';
import { containerLogs, corehub, ensureChronosOverlay, fakeNow } from '../lib/stack.mjs';
import { sleep, utc } from '../lib/util.mjs';
import { at, commandTimes, harmlessJob, iso, mustCreate, ROME, setJobTimezone } from './dst-lib.mjs';

// GSSD-1457, the upgrade path: a database written by the published GA image is opened by the build
// under test. Older Chronos stored next_run as the job-timezone wall clock with the offset dropped
// (a Europe/Rome 14:30 CEST as 14:30, read back as 14:30 UTC); the fixed build stores UTC and converts
// the existing rows once at startup (migrate_next_run_to_utc.py, marker next_run_stored_utc).
//
// Outside DST, process TZ and global timezone setting Europe/Rome. Three jobs:
//   B  pipeline_stop                 daily 14:30 Rome = 12:30Z   missed while down
//   E  pipeline_exit_maintenance     daily 14:35 Rome = 12:35Z   missed while down
//   C  pipeline_stop                 daily 18:00 Rome = 16:00Z   not missed
// The GA instance is stopped before 12:25Z; the build under test starts at 12:50Z, 20 and 15 minutes
// after B and E were due (inside the 3600 s grace).
const T0 = '2026-06-10T12:00:00Z';
const T1 = '2026-06-10T12:50:00Z';

// The release to upgrade from: SANDBOX_UPGRADE_FROM, else the GA version the update endpoint serves.
const resolveGaVersion = async () => {
  if (process.env.SANDBOX_UPGRADE_FROM) return process.env.SANDBOX_UPGRADE_FROM;
  let version;
  let why = '';
  try {
    // Plain fetch: lib/util.mjs `request` trusts only the sandbox CA.
    const r = await fetch('https://api.backoffice.molo17.com/agent/chronos/version', { signal: AbortSignal.timeout(15000) });
    version = (await r.json())?.latestVersionGA;
    why = `HTTP ${r.status}`;
  } catch (err) {
    why = err.message;
  }
  if (!version) throw new Error(`cannot resolve the GA version to upgrade from (version endpoint: ${why}); set SANDBOX_UPGRADE_FROM=<tag>`);
  return version;
};

// The API serialises next_run as an ISO string; one without an offset is UTC, as every reader treats it.
const instant = s => at(/(Z|[+-]\d\d:?\d\d)$/.test(s) ? s : `${s}Z`);

export default {
  name: 'upgrade-from-ga',
  description: 'GSSD-1457 upgrade: a database written by the published GA image is opened by the build under test; next_run converted to UTC, runs missed during the upgrade replayed',
  async run(ctx) {
    const version = await resolveGaVersion();
    const gaImage = await ensureChronosOverlay(version);
    ctx.note(`upgrading from GA ${version} (${gaImage}) to ${env.imageSpec}`);

    // resetData: the volume of an earlier run already carries the migration marker.
    let inst = await ctx.startInstance({ label: 'upgrade', tz: ROME, clockUtc: T0, image: gaImage, resetData: true });
    let api = ctx.api(inst);
    ctx.note(`GA ${version} ready at ${utc(fakeNow(inst))}`);
    await setJobTimezone(api, ROME);
    const b = await mustCreate(api, harmlessJob('B daily 14:30 Rome', '30 14 * * *', 'auto-upgrade-b'));
    const e = await mustCreate(api, jobSpec('pipeline_exit_maintenance', { name: 'E daily 14:35 Rome', cron_expression: '35 14 * * *', pipeline_id: 'auto-upgrade-e' }));
    const c = await mustCreate(api, harmlessJob('C daily 18:00 Rome', '0 18 * * *', 'auto-upgrade-c'));
    const nextRuns = async () => ({
      B: (await api.getJob(b.id)).json?.next_run,
      E: (await api.getJob(e.id)).json?.next_run,
      C: (await api.getJob(c.id)).json?.next_run,
    });
    const gaNext = await nextRuns();
    // The GA API shows the stored wall clock as if it were UTC (16:30+02:00 for B), the GA scheduler runs it at 12:30Z.
    ctx.note(`GA API next_run: ${JSON.stringify(gaNext)}`);
    if (fakeNow(inst) > at('2026-06-10T12:25:00Z')) {
      ctx.check('setup: GA incarnation ended before the first fire time', false, utc(fakeNow(inst)));
      return;
    }

    // Mark before the restart: the replay runs right at startup, before the instance is reported ready.
    // The GA incarnation never reaches 12:30Z, so it makes no call that could be counted.
    const since = await corehub.mark();
    inst = await ctx.restartInstance(inst, { clockUtc: T1, graceSec: 5 });
    api = ctx.api(inst);
    ctx.note(`build under test ready at ${utc(fakeNow(inst))}`);
    await sleep(60000);
    const calls = await ctx.calls(since, { tag: inst.tag });
    const runsB = commandTimes(calls, 'auto-upgrade-b', 'sync/stop');
    const runsE = commandTimes(calls, 'auto-upgrade-e', 'maintenance/exit');
    const after = await nextRuns();
    const logs = await containerLogs(inst.name);
    // A GA that already carries the migration (0.3.2 and later) wrote the marker itself: the build under test
    // then logs that it skips. Either line means next_run_stored_utc is in place after the upgrade.
    const migrationLogged = logs.split('\n').find(l => /Converted next_run to UTC on \d+ existing jobs|next_run already stored as UTC, skipping/.test(l));
    const evidence = {
      gaVersion: version,
      gaNextRun: gaNext,
      upgradedAt: iso(at(T1)),
      missedFireTimes: { B: '2026-06-10T12:30:00Z', E: '2026-06-10T12:35:00Z' },
      runsAfterUpgrade: { B: runsB.map(iso), E: runsE.map(iso) },
      nextRunAfterUpgrade: after,
      migrationLog: migrationLogged || null,
    };
    const finding = {
      id: 'GSSD-1457-next-run-wall-clock-after-upgrade',
      title: 'After an upgrade from the GA image, next_run values written as job-local wall time are read as UTC: missed runs are lost and next runs shift',
      severity: 'defect',
      detail: 'Chronos before the fix stored next_run as the job-timezone wall clock without the offset. A build that reads it as UTC computes the catch-up and the API next_run two hours off for Europe/Rome in summer.',
      evidence,
    };
    ctx.check('the missed 12:30Z run of B is executed exactly once after the upgrade', runsB.length === 1, `B runs after the upgrade: ${runsB.map(iso).join(', ') || 'none'}`, { finding, evidence });
    ctx.check('the missed 12:35Z exit-maintenance of E is executed exactly once after the upgrade', runsE.length === 1, `E runs after the upgrade: ${runsE.map(iso).join(', ') || 'none'}`, { finding, evidence });
    ctx.check('C keeps the instant the GA scheduler runs it (2026-06-10T16:00:00Z, not 18:00Z)', iso(instant(after.C)) === '2026-06-10T16:00:00.000Z', `API next_run ${after.C}`, { finding, evidence });
    ctx.check('B is rescheduled for 2026-06-11T12:30:00Z', iso(instant(after.B)) === '2026-06-11T12:30:00.000Z', `API next_run ${after.B}`, { finding, evidence });
    ctx.check('the next_run migration ran (marker next_run_stored_utc)', !!migrationLogged, migrationLogged || 'neither "Converted next_run to UTC" nor "next_run already stored as UTC" in the container log', { finding, evidence });
    ctx.save('summary.json', evidence);
  },
};
