import fs from 'node:fs';
import path from 'node:path';
import { writeJson } from './util.mjs';

const secs = ms => (typeof ms === 'number' ? `${Math.round(ms / 1000)}s` : '-');

const fence = v => {
  const s = typeof v === 'string' ? v : JSON.stringify(v, null, 2);
  return `\`\`\`\n${s.length > 2500 ? `${s.slice(0, 2500)}\n...` : s}\n\`\`\``;
};

export const scenarioStatus = s => (s.error ? 'ERROR' : s.checks.some(c => !c.pass) ? 'FAIL' : 'PASS');

export const writeReport = (dir, run) => {
  const scenarios = run.scenarios.map(s => ({ ...s, status: scenarioStatus(s) }));
  const findings = scenarios.flatMap(s => s.findings.map(f => ({ ...f, scenario: s.name })));
  writeJson(path.join(dir, 'report.json'), { ...run, scenarios, findings });

  const l = [];
  l.push(`# Chronos sandbox run ${path.basename(dir)}`, '');
  l.push(`Started ${run.startedAt}${run.finishedAt ? `, finished ${run.finishedAt}` : ' (in progress)'}.`, '');
  l.push('| Setting | Value |', '|---|---|');
  Object.entries(run.config).forEach(([k, v]) => l.push(`| ${k} | ${v} |`));
  l.push('');

  const count = st => scenarios.filter(s => s.status === st).length;
  l.push(`## Summary: ${count('PASS')} pass, ${count('FAIL')} fail, ${count('ERROR')} error`, '');
  l.push('| Scenario | Status | Checks | Findings | Time |', '|---|---|---|---|---|');
  scenarios.forEach(s => {
    const failed = s.checks.filter(c => !c.pass).length;
    l.push(`| [${s.name}](#${s.name}) | ${s.status} | ${s.checks.length - failed}/${s.checks.length} | ${s.findings.length || '-'} | ${secs(s.durationMs)} |`);
  });
  l.push('');

  l.push('## Findings', '');
  if (!findings.length) l.push('None.', '');
  findings.forEach(f => {
    l.push(`### ${f.severity.toUpperCase()}: ${f.title} (\`${f.id}\`, scenario ${f.scenario})`, '');
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
    s.notes.forEach(n => l.push(`- ${n}`));
    if (s.notes.length) l.push('');
    l.push(`Artifacts: \`${path.relative(path.dirname(dir), s.dir)}\``, '');
  });
  fs.writeFileSync(path.join(dir, 'report.md'), `${l.join('\n')}\n`);
};
