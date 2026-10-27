import { env } from '../lib/env.mjs';
import { fakeNow } from '../lib/stack.mjs';
import { mapLimit } from '../lib/util.mjs';
import { at, commandTimes, harmlessJob, iso, ROME, romeWall, setJobTimezone, spyTop, stackMentionsScheduler, watch } from './dst-lib.mjs';

// Fall-back night 2026-10-25 (Europe/Rome). Each pattern has the instant (UTC) at
// which its fire time is expected to matter, and the window to observe.
const PATTERNS = [
  { cron: '* * * * *', trigger: '2026-10-25T00:59:00Z', until: '2026-10-25T01:03:00Z', why: 'every minute, 02:59 CEST' },
  { cron: '*/5 * * * *', trigger: '2026-10-25T00:55:00Z', until: '2026-10-25T01:06:00Z', why: 'every 5 minutes, 02:55 CEST' },
  { cron: '0 * * * *', trigger: '2026-10-25T00:00:00Z', until: '2026-10-25T00:04:00Z', why: 'hourly, 02:00 CEST' },
  { cron: '30 2 * * *', trigger: '2026-10-25T00:30:00Z', until: '2026-10-25T00:34:00Z', why: 'daily 02:30, first 02:30 CEST' },
  { cron: '59 2 * * *', trigger: '2026-10-25T00:59:00Z', until: '2026-10-25T01:03:00Z', why: 'daily 02:59, first 02:59 CEST' },
  { cron: '50 1 * * 0', trigger: '2026-10-24T23:50:00Z', until: '2026-10-24T23:54:00Z', why: 'customer enter-maintenance, Sunday 01:50 CEST' },
  { cron: '10 3 * * 0', trigger: '2026-10-25T02:10:00Z', until: '2026-10-25T02:14:00Z', why: 'customer exit-maintenance, Sunday 03:10 CET' },
];
const SCHED_TZ = [ROME, 'UTC'];
const JOB_TZ = [ROME, null];
const MARGIN_MS = 100000;

const cellName = (p, s, j) => `${p.cron} | sched ${s} | job ${j || 'unset'}`;

const runCell = async (ctx, cell, n) => {
  const label = `pat-${n}`;
  const trigger = at(cell.trigger);
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    const margin = MARGIN_MS + (attempt - 1) * 90000;
    const inst = await ctx.startInstance({ label, tz: cell.schedTz, clockUtc: trigger - margin, resetData: attempt > 1 });
    try {
      const api = ctx.api(inst);
      if (cell.jobTz) await setJobTimezone(api, cell.jobTz);
      const pipeline = `auto-${label}`;
      const created = await api.createJob(harmlessJob(`pattern ${cell.cron}`, cell.cron, pipeline));
      const readyAt = fakeNow(inst);
      if (created.status !== 201) return { ...cell, error: `create failed ${created.status} ${JSON.stringify(created.json)}` };
      if (readyAt > trigger - 15000) {
        ctx.note(`${label}: started too late (${iso(readyAt)}), retrying`);
        continue;
      }
      const result = await watch(ctx, inst, { untilMs: at(cell.until), label, postFreezeMs: 10000, failsToFreeze: 3, maxRealMs: 12 * 60 * 1000 });
      const runs = commandTimes(result.calls, pipeline, 'sync/stop');
      const job = (await api.getJob(created.json.id)).json;
      return {
        ...cell,
        frozen: result.frozen,
        lastApiAnswer: result.lastOkFake ? iso(result.lastOkFake) : null,
        lastApiAnswerRome: result.lastOkFake ? romeWall(result.lastOkFake) : null,
        runs: runs.map(iso),
        runCount: runs.length,
        jobTimezone: created.json.timezone_name,
        spy: result.frozen ? spyTop(result.evidence.pySpy) : null,
        schedulerStack: result.frozen ? stackMentionsScheduler(result.evidence.pySpy) : null,
        cpu: result.evidence.stats?.CPUPerc || null,
        nextRunApi: job?.next_run || null,
      };
    } finally {
      if (!ctx.options.keep) await ctx.stopInstance(inst);
    }
  }
  return { ...cell, error: 'could not start early enough twice' };
};

export default {
  name: 'dst-fallback-patterns',
  description: 'GSSD-1457: which cron patterns freeze on the fall-back night, by scheduler TZ (Europe/Rome, UTC) and job timezone (Europe/Rome, unset)',
  async run(ctx) {
    const filter = ctx.options.patterns ? ctx.options.patterns.split(';').map(s => s.trim()) : null;
    const cells = [];
    PATTERNS.filter(p => !filter || filter.includes(p.cron)).forEach(p => SCHED_TZ.forEach(s => JOB_TZ.forEach(j => cells.push({ ...p, schedTz: s, jobTz: j, name: cellName(p, s, j) }))));
    const concurrency = ctx.options.concurrency || 6;
    ctx.note(`${cells.length} cell(s), ${concurrency} at a time, image ${env.chronosImageTag()}`);
    const results = await mapLimit(cells, concurrency, async (cell, i) => {
      try {
        const r = await runCell(ctx, cell, i);
        const state = r.error ? `error ${r.error}` : r.frozen ? `FROZEN after ${r.lastApiAnswerRome}` : `ok, ${r.runCount} run(s)`;
        ctx.note(`${cell.name}: ${state}`);
        return r;
      } catch (e) {
        ctx.note(`${cell.name}: runner error ${e.message}`);
        return { ...cell, error: e.message };
      }
    });
    ctx.save('matrix.json', results);
    ctx.save('matrix.md', matrixMarkdown(results));

    const frozen = results.filter(r => r.frozen);
    const evidence = { frozenCells: frozen.map(r => ({ cron: r.cron, schedulerTz: r.schedTz, jobTz: r.jobTz || 'unset', lastApiAnswer: r.lastApiAnswerRome, spy: r.spy })) };
    const finding = {
      id: 'GSSD-1457-dst-freeze-patterns',
      title: `${frozen.length} of ${results.length} cron/timezone cells freeze Chronos on the fall-back night`,
      severity: 'defect',
      detail: 'See matrix.md in the scenario folder: every frozen cell stops answering HTTP and spins on one CPU inside APScheduler.',
      evidence,
    };
    results.forEach(r => {
      if (r.error) ctx.check(`${r.name}: cell ran`, false, r.error);
      else ctx.check(`${r.name}: Chronos stays responsive`, !r.frozen, r.frozen ? `frozen after ${r.lastApiAnswerRome}; ${r.spy}` : `${r.runCount} run(s), last API answer ${r.lastApiAnswerRome}`, r.frozen ? { finding, evidence } : {});
    });
  },
};

const matrixMarkdown = results => {
  const rows = ['| cron | scheduler TZ | job TZ | result | last API answer (Rome) | runs | top frame |', '|---|---|---|---|---|---|---|'];
  results.forEach(r => rows.push(`| \`${r.cron}\` | ${r.schedTz} | ${r.jobTz || 'unset'} | ${r.error ? `ERROR ${r.error}` : r.frozen ? '**FROZEN**' : 'ok'} | ${r.lastApiAnswerRome || '-'} | ${r.runCount ?? '-'} | ${r.spy || ''} |`));
  return rows.join('\n');
};
