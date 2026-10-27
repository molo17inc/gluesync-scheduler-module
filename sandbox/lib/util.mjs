import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import { env } from './env.mjs';

export const sleep = ms => new Promise(r => setTimeout(r, ms));
export const nowIso = () => new Date().toISOString();

let logStream = null;
export const setLogFile = file => {
  logStream = fs.createWriteStream(file, { flags: 'a' });
};

export const say = (msg, extra) => {
  const line = `[${new Date().toISOString().slice(11, 19)}] ${msg}${extra ? ` ${JSON.stringify(extra)}` : ''}`;
  process.stdout.write(`${line}\n`);
  logStream?.write(`${line}\n`);
};

// Runs a command and never throws: the result carries code, out, err and elapsed ms.
export const run = (cmd, args, opts = {}) =>
  new Promise(resolve => {
    const started = Date.now();
    const child = spawn(cmd, args, {
      cwd: opts.cwd,
      env: { ...process.env, ...(opts.env || {}) },
      stdio: [opts.input ? 'pipe' : 'ignore', 'pipe', 'pipe'],
    });
    let out = '';
    let err = '';
    child.stdout.on('data', d => {
      out += d;
      if (opts.stream) process.stdout.write(d);
    });
    child.stderr.on('data', d => {
      err += d;
      if (opts.stream) process.stderr.write(d);
    });
    if (opts.input) {
      child.stdin.write(opts.input);
      child.stdin.end();
    }
    const timer = opts.timeoutMs
      ? setTimeout(() => {
          child.kill('SIGKILL');
          err += `\n[timeout after ${opts.timeoutMs}ms]`;
        }, opts.timeoutMs)
      : null;
    child.on('error', e => {
      err += String(e);
    });
    child.on('close', code => {
      if (timer) clearTimeout(timer);
      resolve({ code, out: out.trim(), err: err.trim(), ms: Date.now() - started });
    });
  });

export const mustRun = async (cmd, args, opts = {}) => {
  const r = await run(cmd, args, opts);
  if (r.code !== 0) throw new Error(`${cmd} ${args.slice(0, 6).join(' ')} failed (${r.code}): ${(r.err || r.out).slice(-2000)}`);
  return r;
};

export const docker = (args, opts = {}) => run('docker', args, opts);
export const mustDocker = (args, opts = {}) => mustRun('docker', args, opts);

// JSON over HTTP(S). Never throws: transport errors come back as status 0 + error.
export const request = (url, { method = 'GET', headers = {}, body, timeoutMs = 15000, ca } = {}) =>
  new Promise(resolve => {
    const started = Date.now();
    const u = new URL(url);
    const lib = u.protocol === 'https:' ? https : http;
    const payload = body === undefined ? undefined : JSON.stringify(body);
    const req = lib.request(
      u,
      {
        method,
        agent: false,
        headers: {
          ...(payload ? { 'content-type': 'application/json', 'content-length': Buffer.byteLength(payload) } : {}),
          ...headers,
        },
        ca: u.protocol === 'https:' ? ca || safeCa() : undefined,
        timeout: timeoutMs,
      },
      res => {
        const chunks = [];
        res.on('data', c => chunks.push(c));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          let parsed;
          try {
            parsed = text ? JSON.parse(text) : undefined;
          } catch {
            parsed = undefined;
          }
          resolve({ status: res.statusCode, headers: res.headers, json: parsed, text, ms: Date.now() - started });
        });
      },
    );
    req.on('timeout', () => req.destroy(new Error(`timeout after ${timeoutMs}ms`)));
    req.on('error', e => resolve({ status: 0, error: e.message, ms: Date.now() - started }));
    if (payload) req.write(payload);
    req.end();
  });

const safeCa = () => {
  try {
    return env.caPem();
  } catch {
    return undefined;
  }
};

export const waitFor = async (label, fn, { timeoutMs = 60000, intervalMs = 2000 } = {}) => {
  const started = Date.now();
  let last;
  while (Date.now() - started < timeoutMs) {
    try {
      last = await fn();
      if (last) return { ok: true, value: last, ms: Date.now() - started };
    } catch (e) {
      last = e.message;
    }
    await sleep(intervalMs);
  }
  return { ok: false, label, last, ms: Date.now() - started };
};

export const writeJson = (file, value) => fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);

export const utc = ms => new Date(ms).toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z');

// Wall-clock time in an IANA zone, e.g. "2026-10-25 02:59:00 CEST".
export const inZone = (ms, zone) => {
  const parts = new Intl.DateTimeFormat('sv-SE', {
    timeZone: zone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZoneName: 'short',
    hour12: false,
  }).formatToParts(new Date(ms));
  const g = t => parts.find(p => p.type === t)?.value;
  return `${g('year')}-${g('month')}-${g('day')} ${g('hour')}:${g('minute')}:${g('second')} ${g('timeZoneName')}`;
};

export const mapLimit = async (items, limit, fn) => {
  const results = new Array(items.length);
  let next = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) {
      const i = next;
      next += 1;
      results[i] = await fn(items[i], i);
    }
  });
  await Promise.all(workers);
  return results;
};
