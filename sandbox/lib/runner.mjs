import fs from 'node:fs';
import path from 'node:path';
import { ScenarioContext } from './context.mjs';
import { env } from './env.mjs';
import { writeReport } from './report.mjs';
import { adoptMain, corehub, instances, up } from './stack.mjs';
import { docker, nowIso, say, setLogFile } from './util.mjs';
import { scenarios } from '../scenarios/index.mjs';

export const listScenarios = () => {
  scenarios.forEach(s => process.stdout.write(`${s.name.padEnd(28)} ${s.description}\n`));
};

const pickScenarios = names => {
  if (!names.length) return scenarios;
  return names.map(n => {
    const s = scenarios.find(x => x.name === n);
    if (!s) throw new Error(`unknown scenario "${n}"; known: ${scenarios.map(x => x.name).join(', ')}`);
    return s;
  });
};

const stackUp = async () => {
  const hubOk = await corehub.isUp().catch(() => false);
  const running = hubOk ? (await docker(['inspect', '-f', '{{.Config.Image}}', env.mainContainer])).out : '';
  if (!hubOk || running !== env.chronosImageTag()) {
    say(hubOk ? `main Chronos is ${running || 'missing'}, bringing the sandbox to ${env.chronosImageTag()}` : 'sandbox is not running, starting it');
    await up();
    return;
  }
  await adoptMain();
};

export const runScenarios = async ({ names, keep, concurrency, patterns }) => {
  const selected = pickScenarios(names);
  await stackUp();
  const stamp = nowIso().replace(/[:.]/g, '-').slice(0, 19);
  const label = names.length ? names.join('+') : 'all';
  const dir = path.join(env.reportsDir, `${stamp}-${label}`);
  fs.mkdirSync(dir, { recursive: true });
  setLogFile(path.join(dir, 'run.log'));
  const main = instances.get('main');
  const run = {
    startedAt: nowIso(),
    finishedAt: null,
    config: {
      project: env.name,
      'Chronos image': env.imageSpec + (env.apscheduler ? ` (apscheduler ${env.apscheduler})` : ''),
      'overlay image': env.chronosImageTag(),
      TLS: env.tls,
      ports: `Core Hub ${env.ports.corehub}, Chronos ${env.ports.chronos}, fake admin ${env.ports.admin}`,
      'main instance TZ': main?.tz || 'Europe/Rome',
    },
    scenarios: [],
  };
  say(`report: ${dir}`);

  for (const sc of selected) {
    const sdir = path.join(dir, sc.name);
    const ctx = new ScenarioContext({ name: sc.name, description: sc.description, dir: sdir, options: { keep, concurrency, patterns } });
    say(`== ${sc.name}: ${sc.description}`);
    await corehub.post('/__sandbox/reset');
    const since = await ctx.mark();
    const began = Date.now();
    let error;
    try {
      await sc.run(ctx);
    } catch (e) {
      error = e.stack || String(e);
      say(`  scenario error: ${e.message}`);
    }
    try {
      await corehub.post('/__sandbox/reset');
      if (!(await corehub.isUp().catch(() => false))) await corehub.post('/__sandbox/up');
      await ctx.finalize({ keep, since });
    } catch (e) {
      say(`  finalize error: ${e.message}`);
    }
    run.scenarios.push({
      name: sc.name,
      description: sc.description,
      dir: sdir,
      durationMs: Date.now() - began,
      checks: ctx.checks,
      findings: ctx.findings,
      notes: ctx.notes,
      error,
    });
    writeReport(dir, run);
  }
  run.finishedAt = nowIso();
  writeReport(dir, run);
  const failed = run.scenarios.filter(s => s.error || s.checks.some(c => !c.pass)).length;
  say(`done: ${run.scenarios.length - failed} pass, ${failed} fail/error. Report: ${path.join(dir, 'report.md')}`);
  if (failed) process.exitCode = 1;
};
