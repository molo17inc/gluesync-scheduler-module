import fs from 'node:fs';
import path from 'node:path';
import { evaluate, loadKnown } from './gate.mjs';
import { writeJson } from './util.mjs';

const secs = ms => (typeof ms === 'number' ? `${Math.round(ms / 1000)}s` : '-');

const fence = v => {
  const s = typeof v === 'string' ? v : JSON.stringify(v, null, 2);
  return `\`\`\`\n${s.length > 2500 ? `${s.slice(0, 2500)}\n...` : s}\n\`\`\``;
};

const xml = v => String(v ?? '').replace(/[^\t\n\r\u0020-\ud7ff\ue000-\ufffd]/g, '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// One testsuite per scenario, one testcase per check. A known finding is `skipped` with the finding
// and its note as the message: visible in the pipeline's test report, but not a failure. New findings,
// checks without a finding and `setup:` checks are failures, a runner error is an error, and a stale
// known finding is a failing testcase of its own.
const junit = scenarios => {
  const x = ['<?xml version="1.0" encoding="UTF-8"?>', '<testsuites>'];
  scenarios.forEach(s => {
    const cases = [];
    s.checks.forEach(c => {
      const attrs = `classname="${xml(s.name)}" name="${xml(c.id)}"`;
      const body = xml(c.detail);
      if (c.verdict === 'pass') cases.push(`    <testcase ${attrs}/>`);
      else if (c.verdict === 'known') cases.push(`    <testcase ${attrs}><skipped message="${xml(`known finding ${c.finding}: ${c.note}`)}"/></testcase>`);
      else if (c.verdict === 'setup') cases.push(`    <testcase ${attrs}><error message="sandbox setup failed" type="setup">${body}</error></testcase>`);
      else if (c.verdict === 'new') cases.push(`    <testcase ${attrs}><failure message="${xml(`new finding ${c.finding}, not in sandbox/known-findings.json`)}" type="new-finding">${body}</failure></testcase>`);
      else cases.push(`    <testcase ${attrs}><failure message="check failed without a finding" type="no-finding">${body}</failure></testcase>`);
    });
    if (s.error) cases.push(`    <testcase classname="${xml(s.name)}" name="scenario ran to the end"><error message="runner error" type="runner">${xml(s.error)}</error></testcase>`);
    s.stale.forEach(k => cases.push(`    <testcase classname="${xml(s.name)}" name="${xml(`known finding ${k.id} still occurs`)}"><failure message="stale entry (fixed? remove it from sandbox/known-findings.json)" type="stale-known-finding">${xml(k.note)}</failure></testcase>`));
    const count = v => cases.filter(c => c.includes(v)).length;
    x.push(`  <testsuite name="${xml(s.name)}" tests="${cases.length}" failures="${count('<failure')}" errors="${count('<error')}" skipped="${count('<skipped')}" time="${((s.durationMs || 0) / 1000).toFixed(1)}">`, ...cases, '  </testsuite>');
  });
  x.push('</testsuites>', '');
  return x.join('\n');
};

export const writeReport = (dir, run) => {
  const known = loadKnown();
  const verdict = evaluate(run, known);
  const { scenarios, problems } = verdict;
  const findings = scenarios.flatMap(s => s.findings.map(f => ({ ...f, scenario: s.name, known: !!known?.[f.id] })));
  writeJson(path.join(dir, 'report.json'), { ...run, scenarios, findings, gate: { ok: problems.length === 0, knownFindingsApplied: !!known, problems } });
  fs.writeFileSync(path.join(dir, 'junit.xml'), junit(scenarios));

  const l = [];
  l.push(`# Chronos sandbox run ${path.basename(dir)}`, '');
  l.push(`Started ${run.startedAt}${run.finishedAt ? `, finished ${run.finishedAt}` : ' (in progress)'}.`, '');
  l.push('| Setting | Value |', '|---|---|');
  Object.entries(run.config).forEach(([k, v]) => l.push(`| ${k} | ${v} |`));
  l.push('');

  const count = st => scenarios.filter(s => s.status === st).length;
  l.push(`## Summary: ${count('PASS')} pass, ${count('KNOWN')} known findings only, ${count('FAIL')} fail, ${count('ERROR')} error`, '');
  l.push(
    problems.length ? `**Gate: FAILED** (${problems.length})` : '**Gate: ok**',
    known ? '(known findings from sandbox/known-findings.json apply)' : '(known-findings.json does not apply to this image: every failed check counts)',
    '',
    ...problems.map(p => `- ${p}`),
    '',
  );
  l.push('| Scenario | Status | Checks | Findings | Time |', '|---|---|---|---|---|');
  scenarios.forEach(s => {
    const failed = s.checks.filter(c => !c.pass).length;
    l.push(`| [${s.name}](#${s.name}) | ${s.status} | ${s.checks.length - failed}/${s.checks.length} | ${s.findings.length || '-'} | ${secs(s.durationMs)} |`);
  });
  l.push('');

  l.push('## Findings', '');
  if (!findings.length) l.push('None.', '');
  findings.forEach(f => {
    l.push(`### ${f.severity.toUpperCase()}: ${f.title} (\`${f.id}\`, scenario ${f.scenario}${f.known ? ', known' : ''})`, '');
    if (f.known) l.push(`Known finding, accepted for now: ${known[f.id].note}`, '');
    if (f.detail) l.push(String(f.detail), '');
    if (f.evidence !== undefined) l.push(fence(f.evidence), '');
  });

  l.push('## Scenarios', '');
  scenarios.forEach(s => {
    l.push(`### ${s.name}`, '');
    l.push(`**${s.status}** - ${s.description}`, '');
    if (s.error) l.push(`Runner error: \`${String(s.error).split('\n')[0]}\``, '');
    l.push('| Check | Result | Detail |', '|---|---|---|');
    [...s.checks].sort((a, b) => Number(a.pass) - Number(b.pass)).forEach(c => l.push(`| ${c.id} | ${c.pass ? 'pass' : '**FAIL**'} | ${String(c.detail).replace(/\|/g, '\\|').replace(/\n/g, ' ').slice(0, 300)} |`));
    l.push('');
    s.stale.forEach(k => l.push(`- **Stale known finding \`${k.id}\`**: it no longer occurs here (fixed? remove it from sandbox/known-findings.json)`));
    s.notes.forEach(n => l.push(`- ${n}`));
    if (s.notes.length || s.stale.length) l.push('');
    l.push(`Artifacts: \`${path.relative(path.dirname(dir), s.dir)}\``, '');
  });
  fs.writeFileSync(path.join(dir, 'report.md'), `${l.join('\n')}\n`);
};
