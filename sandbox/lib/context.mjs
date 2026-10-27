import fs from 'node:fs';
import path from 'node:path';
import { ChronosApi } from './chronos.mjs';
import { env } from './env.mjs';
import { adoptMain, containerLogs, corehub, instances, startChronos, stopChronos, waitReady } from './stack.mjs';
import { nowIso, say, writeJson } from './util.mjs';

const compactCall = e => {
  const qs = e.query && Object.keys(e.query).length ? `?${new URLSearchParams(e.query)}` : '';
  return `${e.method} ${e.path}${qs}`;
};

// Matches "POST /pipelines/x/commands/sync/start entity=e1" against a recorded call.
const matches = (spec, e) => {
  const [method, p, ...kv] = spec.trim().split(/\s+/);
  if (e.method !== method || e.path !== p) return false;
  return kv.every(pair => {
    const i = pair.indexOf('=');
    return String(e.query?.[pair.slice(0, i)]) === pair.slice(i + 1);
  });
};

// One scenario run: collects checks, findings, the API transcript and the artifacts
// written to the scenario folder.
export class ScenarioContext {
  constructor({ name, description, dir, options }) {
    this.name = name;
    this.description = description;
    this.dir = dir;
    this.options = options || {};
    this.checks = [];
    this.findings = [];
    this.notes = [];
    this.transcript = [];
    this.created = [];
    this.startedAt = nowIso();
    this.logGen = new Map();
    fs.mkdirSync(dir, { recursive: true });
  }

  // ---- results

  check(id, ok, detail, { evidence, finding } = {}) {
    // `finding` on a failed check is the id of the defect it demonstrates (lib/gate.mjs reads it).
    const entry = { id, pass: !!ok, detail: detail || '', evidence, ...(!ok && finding ? { finding: finding.id || id } : {}) };
    this.checks.push(entry);
    say(`  ${ok ? 'PASS' : 'FAIL'} ${id}${detail ? ` - ${String(detail).split('\n')[0].slice(0, 200)}` : ''}`);
    if (!ok && finding) this.finding({ id: finding.id || id, title: finding.title, severity: finding.severity || 'defect', detail: finding.detail || detail, evidence: finding.evidence || evidence });
    return !!ok;
  }

  finding({ id, title, severity = 'defect', detail, evidence }) {
    if (!this.findings.some(f => f.id === id)) this.findings.push({ id, title, severity, detail, evidence });
  }

  note(msg) {
    this.notes.push(msg);
    say(`  note: ${msg}`);
  }

  save(file, content) {
    const target = path.join(this.dir, file);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    if (typeof content === 'string') fs.writeFileSync(target, content.endsWith('\n') ? content : `${content}\n`);
    else writeJson(target, content);
  }

  // ---- Core Hub observation

  async mark() {
    return corehub.mark();
  }

  // HTTP calls the fake recorded since `mark` for a module tag, as compact entries.
  async calls(since, { tag, ignore = /^\/authentication\//, only } = {}) {
    const items = await corehub.log({ since, tag, kind: 'http' });
    return items.filter(e => !ignore.test(e.path) && (!only || only.test(`${e.method} ${e.path}`)));
  }

  describe(items) {
    return items.map(e => `${e.fakeTs || e.ts} ${compactCall(e)} -> ${e.status}${e.fault ? ` [${e.fault}]` : ''}`);
  }

  // Ordered, exact comparison of the recorded calls with the expected list.
  expectCalls(id, items, expected, { exact = true, finding } = {}) {
    const actual = items.map(compactCall);
    let ok;
    if (exact) ok = items.length === expected.length && expected.every((spec, i) => matches(spec, items[i]));
    else {
      let at = 0;
      ok = expected.every(spec => {
        const hit = items.findIndex((e, i) => i >= at && matches(spec, e));
        if (hit === -1) return false;
        at = hit + 1;
        return true;
      });
    }
    return this.check(id, ok, ok ? `${expected.length} expected Core Hub call(s) seen` : `expected ${JSON.stringify(expected)} but Core Hub saw ${JSON.stringify(actual)}`, {
      evidence: { expected, actual: this.describe(items) },
      finding,
    });
  }

  // ---- Chronos instances

  api(inst) {
    return new ChronosApi(inst, this.transcript);
  }

  async main() {
    const inst = await adoptMain();
    return { inst, api: this.api(inst) };
  }

  // Remove every job and trigger flow from the main instance so scenarios start clean.
  async resetMain() {
    const { inst, api } = await this.main();
    const jobs = await api.get('list jobs (reset)', '/api/jobs/', { query: { limit: 1000 } });
    for (const j of jobs.json?.items || []) await api.del(`reset: delete job ${j.id}`, `/api/jobs/${j.id}`);
    const flows = await api.get('list triggers (reset)', '/api/triggers/', { query: { limit: 1000 } });
    for (const f of flows.json?.items || []) await api.del(`reset: delete trigger ${f.id}`, `/api/triggers/${f.id}`);
    return { inst, api };
  }

  async startInstance(opts) {
    const inst = await startChronos(opts);
    this.created.push(inst);
    await waitReady(inst);
    return inst;
  }

  async stopInstance(inst, { graceSec = 5 } = {}) {
    if (inst.stopped) return;
    await this.saveInstanceLogs(inst).catch(() => {});
    await stopChronos(inst, { graceSec }).catch(() => {});
    inst.stopped = true;
    if (instances.get(inst.label) === inst) instances.delete(inst.label);
  }

  // Recreate the container on the same data volume, optionally with a new clock.
  async restartInstance(inst, { clockUtc, graceSec = 10, env: extraEnv, tz } = {}) {
    await this.saveInstanceLogs(inst);
    await stopChronos(inst, { graceSec });
    inst.stopped = true;
    const next = await startChronos({ label: inst.label, tz: tz || inst.tz, clockUtc, env: { ...(inst.extraEnv || {}), ...(extraEnv || {}) }, tag: inst.tag, fixedPort: inst.label === 'main' ? env.ports.chronos : undefined });
    next.extraEnv = { ...(inst.extraEnv || {}), ...(extraEnv || {}) };
    this.created.push(next);
    await waitReady(next);
    return next;
  }

  async saveInstanceLogs(inst, suffix = '') {
    const gen = (this.logGen.get(inst.label) || 0) + 1;
    this.logGen.set(inst.label, gen);
    const logs = await containerLogs(inst.name, inst.label === 'main' ? { since: this.startedAt } : {});
    this.save(`chronos-${inst.label}-g${gen}${suffix}.log`, logs);
    return logs;
  }

  async finalize({ keep, since }) {
    for (const inst of this.created.filter(i => i.label !== 'main' && !i.stopped)) {
      if (keep) await this.saveInstanceLogs(inst).catch(() => {});
      else await this.stopInstance(inst);
    }
    const main = instances.get('main');
    if (main && this.usedMain) await this.saveInstanceLogs(main).catch(() => {});
    const slice = await corehub.log({ since });
    this.save('corehub-calls.json', slice.filter(e => !(e.kind === 'ws' && e.event === 'ping')));
    this.save('corehub-ws-pings.json', summarizePings(slice));
    this.save('api.json', this.transcript);
    this.save('checks.json', { checks: this.checks, findings: this.findings, notes: this.notes });
  }
}

const summarizePings = slice => {
  const out = {};
  slice
    .filter(e => e.kind === 'ws' && e.event === 'ping')
    .forEach(e => {
      const k = e.tag || '?';
      out[k] ??= { count: 0, first: e.fakeTs || e.ts, last: e.fakeTs || e.ts, firstReal: e.ts, lastReal: e.ts };
      out[k].count += 1;
      out[k].last = e.fakeTs || e.ts;
      out[k].lastReal = e.ts;
    });
  return out;
};
