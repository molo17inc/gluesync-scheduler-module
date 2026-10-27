import { jobSpec } from '../lib/chronos.mjs';
import { env } from '../lib/env.mjs';
import { containerStats, corehub, fakeNow, inspectState, pySpyDump } from '../lib/stack.mjs';
import { inZone, request, sleep, utc } from '../lib/util.mjs';

export const ROME = 'Europe/Rome';
export const iso = ms => new Date(ms).toISOString();
export const at = s => new Date(s).getTime();

// Fall-back night 2026-10-25: 03:00 CEST (01:00Z) becomes 02:00 CET.
export const romeWall = ms => inZone(ms, ROME);

// A harmless job: stops an auto-created pipeline, which the fake accepts forever.
export const harmlessJob = (name, cron, pipeline) => jobSpec('pipeline_stop', { name, cron_expression: cron, pipeline_id: pipeline });

export const setJobTimezone = async (api, zone) => {
  const r = await api.put('set timezone setting', '/api/settings/timezone', { value: zone });
  if (r.status !== 200) throw new Error(`could not set the timezone setting to ${zone}: ${r.status} ${JSON.stringify(r.json)}`);
};

// Creates a job and returns { id, job }. Fails loudly: DST scenarios depend on it.
export const mustCreate = async (api, spec) => {
  const r = await api.createJob(spec);
  if (r.status !== 201) throw new Error(`creating "${spec.name}" failed: ${r.status} ${JSON.stringify(r.json || r.text)}`);
  return { id: r.json.id, job: r.json };
};

// Watches a Chronos instance until `untilMs` (fake clock) is reached or the process
// stops answering. A tick probes `GET /` (served by the event loop, no auth) and
// pulls new Core Hub log entries for the instance's module tag.
//
// Returns { frozen, lastOkFake, firstFailFake, timeline, calls, pings, evidence }.
export const watch = async (ctx, inst, { untilMs, tickMs = 3000, failsToFreeze = 3, postFreezeMs = 45000, maxRealMs = 15 * 60 * 1000, onFrozen, label } = {}) => {
  const began = Date.now();
  const timeline = [];
  const calls = [];
  const pings = [];
  let since = await corehub.mark();
  let consecutiveFails = 0;
  let lastOk = null;
  let firstFail = null;
  let frozen = false;
  let frozenAtReal = null;
  const evidence = {};

  const pull = async () => {
    const items = await corehub.log({ since, tag: inst.tag });
    items.forEach(e => {
      since = Math.max(since, e.seq);
      if (e.kind === 'ws' && e.event === 'ping') pings.push(e);
      else if (e.kind === 'http' && !/^\/authentication\//.test(e.path)) calls.push(e);
      else if (e.kind === 'ws') calls.push(e);
    });
  };

  while (Date.now() - began < maxRealMs) {
    const t0 = Date.now();
    const r = await request(`${env.scheme}://127.0.0.1:${inst.hostPort}/`, { timeoutMs: 2500 });
    const fake = fakeNow(inst);
    const ok = r.status > 0;
    timeline.push({ real: iso(t0), fake: iso(fake), status: r.status, ms: r.ms, error: r.error });
    if (ok) {
      consecutiveFails = 0;
      lastOk = fake;
    } else {
      consecutiveFails += 1;
      firstFail ??= fake;
    }
    await pull().catch(() => {});
    if (!frozen && consecutiveFails >= failsToFreeze) {
      frozen = true;
      frozenAtReal = Date.now();
      const state = await inspectState(inst.name);
      const stats = await containerStats(inst.name);
      evidence.containerState = state && { status: state.Status, running: state.Running, exitCode: state.ExitCode, oomKilled: state.OOMKilled };
      evidence.stats = stats;
      evidence.pySpy = await pySpyDump(inst.name);
      if (onFrozen) await onFrozen(evidence);
    }
    if (frozen && Date.now() - frozenAtReal > postFreezeMs) break;
    if (!frozen && untilMs && fake >= untilMs) break;
    const wait = tickMs - (Date.now() - t0);
    if (wait > 0) await sleep(wait);
  }
  await pull().catch(() => {});
  if (frozen) {
    evidence.statsAfter = await containerStats(inst.name);
    evidence.pySpyAfter = await pySpyDump(inst.name);
  }
  const timelineFile = `${label || inst.label}-timeline.json`;
  ctx.save(timelineFile, { frozen, lastOkFake: lastOk && iso(lastOk), firstFailFake: firstFail && iso(firstFail), samples: timeline });
  return { frozen, lastOkFake: lastOk, firstFailFake: firstFail, timeline, calls, pings, evidence };
};

export const spyTop = dump => {
  const m = String(dump || '').match(/Thread \d+ \(active[^)]*\)\n((?:\s+\S.*\n?){1,6})/);
  return m ? m[1].trim().split('\n').map(s => s.trim()).slice(0, 4).join(' <- ') : '';
};

// py-spy lags new Python releases (the repo image floats to the newest Python 3).
export const pySpyUnsupported = dump => /Unsupported version of Python/.test(String(dump || ''));

export const stackMentionsScheduler = dump => /get_next_fire_time|_get_run_times/.test(String(dump || ''));

// Core Hub POSTs to one pipeline's commands, as fake-clock times.
export const commandTimes = (calls, pipeline, action) =>
  calls
    .filter(e => e.kind === 'http' && e.method === 'POST' && e.path === `/pipelines/${pipeline}/commands/${action}`)
    .map(e => at(e.fakeTs || e.ts));

export const fmtRome = ms => `${romeWall(ms)} (${utc(ms)})`;

export const lastPingFake = pings => (pings.length ? at(pings[pings.length - 1].fakeTs || pings[pings.length - 1].ts) : null);
