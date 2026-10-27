import fs from 'node:fs';
import path from 'node:path';
import { env } from './env.mjs';
import { scenarios as allScenarios } from '../scenarios/index.mjs';

// The pass/fail rule of a sandbox run (used for the exit status, report.md and junit.xml).
//
// The sandbox finds real Chronos defects, so on today's code many checks fail. known-findings.json
// lists the findings that are accepted for now; a run is green when nothing else is wrong:
//
//   pass         the check passed
//   known        failed, and the finding it demonstrates is in known-findings.json (shown, not hidden)
//   new          failed with a finding that is not in the list: a new defect, or a regression
//   unexplained  failed with no finding: a sandbox or scenario problem, not a verdict on Chronos
//   setup        a `setup:` check failed: the sandbox could not arrange the situation
//
// A runner error (the scenario threw) fails the run too. And a known finding that a scenario that
// ran no longer reports is stale: the defect is probably fixed, so remove it from the list.

export const knownFile = path.join(env.sandboxDir, 'known-findings.json');

// { id: { scenarios: [...], note } }, or null when the list does not apply: it describes the repo
// build, so it is not used for a published image (--image ...) or another APScheduler.
export const loadKnown = () => {
  if (env.imageSpec !== 'repo' || env.apscheduler) return null;
  const file = JSON.parse(fs.readFileSync(knownFile, 'utf8'));
  const names = new Set(allScenarios.map(s => s.name));
  Object.entries(file.findings || {}).forEach(([id, k]) => {
    if (!k.note || !Array.isArray(k.scenarios) || !k.scenarios.length) throw new Error(`known-findings.json: "${id}" needs a note and a non-empty scenarios list`);
    k.scenarios.forEach(n => {
      if (!names.has(n)) throw new Error(`known-findings.json: "${id}" names the unknown scenario "${n}"`);
    });
  });
  return file.findings || {};
};

const BAD = new Set(['new', 'unexplained', 'setup']);

export const evaluate = (run, known) => {
  const problems = [];
  const scenarios = run.scenarios.map(s => {
    const checks = s.checks.map(c => {
      let verdict = 'pass';
      if (!c.pass) {
        if (c.id.startsWith('setup:')) verdict = 'setup';
        else if (!c.finding) verdict = 'unexplained';
        else verdict = known?.[c.finding] ? 'known' : 'new';
      }
      if (verdict === 'setup') problems.push(`${s.name}: setup failed: ${c.id} (a sandbox problem, not a Chronos verdict)`);
      if (verdict === 'unexplained') problems.push(`${s.name}: check failed without a finding: ${c.id}`);
      if (verdict === 'new') problems.push(`${s.name}: new finding ${c.finding} (check "${c.id}"); if it is accepted for now, add it to sandbox/known-findings.json`);
      return { ...c, verdict, note: verdict === 'known' ? known[c.finding].note : undefined };
    });
    if (s.error) problems.push(`${s.name}: runner error: ${String(s.error).split('\n')[0]}`);

    // Stale entries: only judged for a scenario that ran to the end, and not for a subset of a matrix.
    const reported = new Set(s.findings.map(f => f.id));
    const stale =
      known && !s.error && !run.partial
        ? Object.entries(known)
            .filter(([id, k]) => k.scenarios.includes(s.name) && !reported.has(id))
            .map(([id, k]) => ({ id, note: k.note }))
        : [];
    stale.forEach(x => problems.push(`${s.name}: known finding ${x.id} no longer occurs (fixed? remove it from sandbox/known-findings.json)`));

    const bad = !!s.error || stale.length > 0 || checks.some(c => BAD.has(c.verdict));
    const status = bad ? (s.error ? 'ERROR' : 'FAIL') : checks.some(c => c.verdict === 'known') ? 'KNOWN' : 'PASS';
    return { ...s, checks, stale, status };
  });
  return { scenarios, problems, known };
};
