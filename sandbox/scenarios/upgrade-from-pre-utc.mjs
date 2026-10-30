import { upgradeScenario } from './upgrade-from-ga.mjs';

// GSSD-1457, the conversion itself. Every GA from 0.3.2 on writes next_run as UTC and the marker, so
// upgrade-from-ga no longer reaches the conversion; this scenario upgrades from the last release that
// stored the job-timezone wall clock, so migrate_next_run_to_utc.py must convert the rows.
// SANDBOX_UPGRADE_FROM_PRE_UTC overrides the release.
const LAST_WALL_CLOCK_RELEASE = '0.3.1';

export default upgradeScenario({
  name: 'upgrade-from-pre-utc',
  description: `GSSD-1457 upgrade from ${LAST_WALL_CLOCK_RELEASE}, the last release that stored next_run as wall clock: the build under test converts it to UTC and replays the runs missed during the upgrade`,
  resolveVersion: async () => process.env.SANDBOX_UPGRADE_FROM_PRE_UTC || LAST_WALL_CLOCK_RELEASE,
});
