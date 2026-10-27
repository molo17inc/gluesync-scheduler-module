#!/usr/bin/env node
// Chronos sandbox CLI.
//
//   node run.mjs up [--rebuild]                 build images, start the fake Core Hub and a real Chronos
//   node run.mjs build [--rebuild]              only build the Chronos images (CI builds them once per pipeline)
//   node run.mjs scenario <name...>             run scenarios and write a report
//   node run.mjs all                            run every scenario (exit status: see lib/gate.mjs, known-findings.json)
//   node run.mjs list                           list scenarios
//   node run.mjs status                         containers and fake Core Hub state
//   node run.mjs down [--purge]                 remove the sandbox (run/ and images too with --purge)
//
// Flags: --image repo|<ref>  --apscheduler <version>  --name <project>  --port-base <n>  --keep  --no-tls
// Env:   SANDBOX_NAME (default chronos-sandbox), SANDBOX_PORT_BASE (default 5417),
//        SANDBOX_IMAGE_MIRROR (pull the base images from <registry>/<repo> instead of Docker Hub)

import { configure, env } from './lib/env.mjs';
import { say } from './lib/util.mjs';
import { build, down, status, up } from './lib/stack.mjs';

const [, , command = 'help', ...rest] = process.argv;
const flag = name => rest.includes(`--${name}`);
const option = name => {
  const i = rest.indexOf(`--${name}`);
  return i !== -1 && rest[i + 1] && !rest[i + 1].startsWith('--') ? rest[i + 1] : undefined;
};
const positional = () => {
  const out = [];
  for (let i = 0; i < rest.length; i += 1) {
    if (rest[i].startsWith('--')) {
      if (rest[i + 1] && !rest[i + 1].startsWith('--') && ['image', 'apscheduler', 'name', 'port-base', 'concurrency', 'patterns'].includes(rest[i].slice(2))) i += 1;
    } else out.push(rest[i]);
  }
  return out;
};

configure({
  name: option('name'),
  portBase: option('port-base') ? Number(option('port-base')) : undefined,
  image: option('image'),
  apscheduler: option('apscheduler'),
  tls: flag('no-tls') ? false : undefined,
});

const main = async () => {
  switch (command) {
    case 'up':
      await up({ rebuild: flag('rebuild') });
      break;
    case 'build':
      await build({ rebuild: flag('rebuild') });
      break;
    case 'status':
      await status();
      break;
    case 'down':
      await down({ purge: flag('purge') });
      break;
    case 'scenario':
    case 'all':
    case 'list': {
      const { runScenarios, listScenarios } = await import('./lib/runner.mjs');
      if (command === 'list') listScenarios();
      else {
        await runScenarios({
          names: command === 'all' ? [] : positional(),
          keep: flag('keep'),
          concurrency: option('concurrency') ? Number(option('concurrency')) : undefined,
          patterns: option('patterns'),
        });
      }
      break;
    }
    default:
      process.stdout.write(`usage: node run.mjs <up|scenario|all|list|status|down> [flags]\nproject: ${env.name}\n`);
  }
};

main().catch(e => {
  say(`FAILED: ${e.message}`);
  process.exitCode = 1;
});
