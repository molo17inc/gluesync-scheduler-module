import { fakeNow } from '../lib/stack.mjs';
import { inZone, utc } from '../lib/util.mjs';
import { at, harmlessJob, iso, ROME, romeWall, setJobTimezone } from './dst-lib.mjs';

// The next_run the API answers when a job is created or updated on a DST night. Chronos
// computes it itself (core/timezone_utils.next_cron_fire, since the pytz -> zoneinfo
// change); APScheduler only overwrites it after the job has run, so this value is what
// the UI shows until the first run. The other DST scenarios watch the runs, not this.
//
// Expected instants are written out by hand, one per case, with the rule they follow:
// - a wall time that occurs once is that instant;
// - a wall time that does not exist (spring-forward gap) resolves with the offset in
//   force before the change (fold=0), as pytz localize(is_dst=False) did: 02:30 in
//   Rome on 2026-03-29 is 01:30Z = 03:30 CEST;
// - a wall time that occurs twice (fall-back) matches at both occurrences, and the
//   earliest one after "now" wins.
//
// Every case is checked three ways: the create (or update) answer, a GET right after,
// and a disabled twin (never handed to APScheduler, so purely Chronos's computation).

const LONDON = 'Europe/London';
const NEW_YORK = 'America/New_York';

const NIGHTS = [
  {
    label: 'dst-nr-spring',
    title: 'spring-forward 2026-03-29, scheduler TZ Europe/Rome',
    tz: ROME,
    clockUtc: '2026-03-29T00:20:00Z', // 01:20 CET, 40 min before the gap
    setting: ROME,
    cases: [
      { cron: '30 2 * * *', expect: '2026-03-29T01:30:00Z', why: '02:30 does not exist: 03:30 CEST' },
      { cron: '0 2 * * *', expect: '2026-03-29T01:00:00Z', why: '02:00 does not exist: 03:00 CEST' },
      { cron: '0 3 * * *', expect: '2026-03-29T01:00:00Z', why: 'first wall time after the gap' },
      { cron: '0 12 * * *', expect: '2026-03-29T10:00:00Z', why: 'noon CEST (pytz with croniter said 13:00)' },
      { cron: '*/15 * * * *', expect: '2026-03-29T00:30:00Z', why: 'next quarter hour, before the gap' },
      { cron: '30 1 * * *', zone: LONDON, expect: '2026-03-29T01:30:00Z', why: 'London 01:30 does not exist: 02:30 BST' },
      { cron: '30 2 * * 0', zone: NEW_YORK, expect: '2026-03-29T06:30:00Z', why: 'control: New York changed on 03-08' },
    ],
    updates: [{ from: '30 2 * * *', to: '45 2 * * *', expect: '2026-03-29T01:45:00Z', why: '02:45 does not exist: 03:45 CEST' }],
  },
  {
    label: 'dst-nr-fall1',
    title: 'fall-back 2026-10-25, first 02:xx (CEST), scheduler TZ Europe/Rome',
    tz: ROME,
    clockUtc: '2026-10-25T00:20:00Z', // 02:20 CEST
    setting: ROME,
    cases: [
      { cron: '30 2 * * *', expect: '2026-10-25T00:30:00Z', why: 'first 02:30 (CEST)' },
      { cron: '0 12 * * *', expect: '2026-10-25T11:00:00Z', why: 'noon CET' },
      { cron: '0 3 * * *', expect: '2026-10-25T02:00:00Z', why: '03:00 CET, after both 02:xx' },
      { cron: '*/15 * * * *', expect: '2026-10-25T00:30:00Z', why: 'next quarter hour' },
      { cron: '30 1 * * *', zone: LONDON, expect: '2026-10-25T00:30:00Z', why: 'first London 01:30 (BST)' },
    ],
    updates: [{ from: '0 12 * * *', to: '0 2 * * *', expect: '2026-10-25T01:00:00Z', why: '02:00 CEST is past: second 02:00 (CET)' }],
  },
  {
    label: 'dst-nr-fall2',
    title: 'fall-back 2026-10-25, second 02:xx (CET), scheduler TZ UTC, jobs in Europe/Rome',
    tz: 'UTC',
    clockUtc: '2026-10-25T01:20:00Z', // 02:20 CET
    setting: 'UTC',
    cases: [
      { cron: '30 2 * * *', zone: ROME, expect: '2026-10-25T01:30:00Z', why: 'second 02:30 (CET)' },
      { cron: '0 2 * * *', zone: ROME, expect: '2026-10-26T01:00:00Z', why: 'both 02:00 are past: tomorrow' },
      { cron: '40 1 * * *', zone: ROME, expect: '2026-10-26T00:40:00Z', why: '01:40 is past: tomorrow' },
      { cron: '*/15 * * * *', zone: ROME, expect: '2026-10-25T01:30:00Z', why: 'next quarter hour' },
      { cron: '30 2 * * *', expect: '2026-10-25T02:30:00Z', why: 'no job timezone: the UTC setting' },
    ],
    updates: [],
  },
];

const caseName = c => `${c.cron}${c.zone ? ` [${c.zone}]` : ''}`;
const instant = s => (s ? new Date(s).getTime() : NaN);
const show = (s, zone) => (s ? `${s} (= ${utc(instant(s))}, ${inZone(instant(s), zone)})` : String(s));

const FINDING = {
  id: 'dst-next-run-wrong',
  title: 'The next_run answered on create or update is wrong on a DST night',
  severity: 'defect',
  detail: 'The API shows a next_run that is not the next instant whose wall time in the job timezone matches the cron (or, for a wall time skipped by the spring-forward, the fold=0 instant).',
};

export default {
  name: 'dst-next-run',
  description: 'next_run answered by create and update on both DST nights (Europe/Rome, Europe/London, America/New_York): skipped and repeated wall times',
  async run(ctx) {
    const table = [];
    for (const night of NIGHTS) {
      const inst = await ctx.startInstance({ label: night.label, tz: night.tz, clockUtc: night.clockUtc, resetData: true });
      const api = ctx.api(inst);
      await setJobTimezone(api, night.setting);
      const readyAt = fakeNow(inst);
      ctx.note(`${night.title}: ready at ${utc(readyAt)} (${romeWall(readyAt)})`);
      if (readyAt >= at(night.clockUtc) + 9 * 60 * 1000) throw new Error(`setup: ${night.label} became ready at ${iso(readyAt)}, too late for the cases`);

      const ids = new Map();
      const verify = async (label, c, answered, id) => {
        const zone = c.zone || night.setting;
        const got = await api.getJob(id);
        const ok = instant(answered) === at(c.expect) && instant(got.json?.next_run) === at(c.expect);
        table.push({ night: night.label, case: label, expected: c.expect, answered, afterGet: got.json?.next_run, ok });
        ctx.check(`${night.label} | ${label}: next_run is ${inZone(at(c.expect), zone)} (${c.why})`, ok, `expected ${show(c.expect, zone)}; answered ${show(answered, zone)}; GET ${show(got.json?.next_run, zone)}`, { finding: FINDING });
      };

      for (const c of night.cases) {
        const extra = c.zone ? { timezone_name: c.zone } : {};
        const pipeline = `auto-${night.label}-${ids.size}`;
        const created = await api.createJob({ ...harmlessJob(`nr ${caseName(c)}`, c.cron, pipeline), ...extra });
        if (created.status !== 201) {
          ctx.check(`${night.label} | ${caseName(c)}: job created`, false, `HTTP ${created.status} ${JSON.stringify(created.json || created.text)}`);
          continue;
        }
        ids.set(c.cron + (c.zone || ''), created.json.id);
        await verify(caseName(c), c, created.json.next_run, created.json.id);

        const twin = await api.createJob({ ...harmlessJob(`nr disabled ${caseName(c)}`, c.cron, `${pipeline}-off`), ...extra, enabled: false });
        if (twin.status === 201) await verify(`${caseName(c)} (disabled)`, c, twin.json.next_run, twin.json.id);
        else ctx.check(`${night.label} | ${caseName(c)} (disabled): job created`, false, `HTTP ${twin.status}`);
      }

      for (const u of night.updates) {
        const id = ids.get(u.from);
        if (!id) {
          ctx.check(`${night.label} | update ${u.from} -> ${u.to}: job to update exists`, false, 'create failed above');
          continue;
        }
        const upd = await api.put(`update job ${id} cron`, `/api/jobs/${id}`, { cron_expression: u.to });
        if (upd.status !== 200) {
          ctx.check(`${night.label} | update ${u.from} -> ${u.to}: answered 200`, false, `HTTP ${upd.status} ${JSON.stringify(upd.json || upd.text)}`);
          continue;
        }
        await verify(`update ${u.from} -> ${u.to}`, { ...u, cron: u.to }, upd.json.next_run, id);
      }
      await ctx.stopInstance(inst);
    }
    ctx.save('next-run.json', table);
  },
};
