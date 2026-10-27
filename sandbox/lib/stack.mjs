import fs from 'node:fs';
import path from 'node:path';
import { env } from './env.mjs';
import { docker, mustDocker, nowIso, request, say, waitFor } from './util.mjs';

const CHRONOS_PORT = 1717;

const probeIntervalMs = 2000;

// Real time between `docker run` and a ready API, refined after every start. Used to
// aim the fake clock: the offset is fixed at start, so the clock at readiness is
// target + (actual lead - estimated lead).
let leadMs = 60000;

export const corehubAdminUrl = p => `http://127.0.0.1:${env.ports.admin}${p}`;
export const corehubUrl = p => `${env.scheme}://127.0.0.1:${env.ports.corehub}${p}`;

export const corehub = {
  admin: (method, p, body) =>
    request(corehubAdminUrl(p), { method, body, headers: { 'x-sandbox-admin': env.adminToken }, timeoutMs: 10000 }),
  get: p => corehub.admin('GET', p),
  post: (p, body) => corehub.admin('POST', p, body || {}),
  del: p => corehub.admin('DELETE', p),
  state: async () => (await corehub.get('/__sandbox/state')).json,
  mark: async () => (await corehub.get('/__sandbox/log?limit=1')).json?.lastSeq ?? 0,
  log: async ({ since = 0, tag, kind } = {}) => {
    const qs = new URLSearchParams({ since: String(since), limit: '60000' });
    if (tag) qs.set('tag', tag);
    if (kind) qs.set('kind', kind);
    return (await corehub.get(`/__sandbox/log?${qs}`)).json?.items ?? [];
  },
  addFault: fault => corehub.post('/__sandbox/faults', fault),
  clearFaults: () => corehub.del('/__sandbox/faults'),
  setMaintenance: (id, on) => corehub.post(`/__sandbox/pipelines/${id}/maintenance`, { on }),
  isUp: async () => (await request(corehubAdminUrl('/ready'), { timeoutMs: 3000 })).status === 200,
  login: async (username, ttlSeconds = 86400) => {
    const r = await request(corehubUrl('/authentication/login'), { method: 'POST', body: { username, password: username, ttlSeconds }, timeoutMs: 10000 });
    if (r.status !== 200) throw new Error(`fake Core Hub login failed for ${username}: ${r.status} ${r.error || r.text}`);
    return r.json.token;
  },
};

// ---------------------------------------------------------------- tls + shared files

const shared = (...p) => path.join(env.sharedDir, ...p);

const prepareShared = async () => {
  fs.mkdirSync(env.sharedDir, { recursive: true });
  fs.writeFileSync(shared('gs-license.dat'), env.licenseText);
  fs.writeFileSync(
    shared('security-config.json'),
    `${JSON.stringify({ ssl: { sslCertificatePath: '/opt/gluesync/shared/chronos.p12', certificateAlias: 'chronos', certificatePassword: env.p12Password, certificateKeyPassword: env.p12Password } }, null, 2)}\n`,
  );
  if (fs.existsSync(shared('chronos.p12'))) return;
  say('generating sandbox CA, server certificate and PKCS12 keystore');
  const script = [
    'set -e',
    'apk add --no-cache openssl >/dev/null',
    'cd /out',
    'openssl req -x509 -newkey rsa:2048 -nodes -keyout ca-key.pem -out ca.pem -days 3650 -subj "/CN=Chronos Sandbox CA" 2>/dev/null',
    'openssl req -newkey rsa:2048 -nodes -keyout server-key.pem -out srv.csr -subj "/CN=corehub" 2>/dev/null',
    'printf "subjectAltName=DNS:corehub,DNS:chronos,DNS:localhost,IP:127.0.0.1\\nbasicConstraints=CA:FALSE\\nkeyUsage=digitalSignature,keyEncipherment\\nextendedKeyUsage=serverAuth,clientAuth\\n" > ext.cnf',
    'openssl x509 -req -in srv.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial -out server.pem -days 3650 -extfile ext.cnf 2>/dev/null',
    `openssl pkcs12 -export -in server.pem -inkey server-key.pem -certfile ca.pem -name chronos -out chronos.p12 -passout pass:${env.p12Password}`,
    'rm -f srv.csr ext.cnf ca.srl',
    'chmod 644 *.pem *.p12',
  ].join(' && ');
  await mustDocker(['run', '--rm', '-v', `${env.sharedDir}:/out`, 'alpine:3.20', 'sh', '-c', script], { timeoutMs: 300000 });
};

// ---------------------------------------------------------------- images

const imageExists = async ref => (await docker(['image', 'inspect', ref])).code === 0;

const buildImages = async ({ rebuild = false } = {}) => {
  await mustDocker(['build', '-q', '-t', env.fakeImage, path.join(env.sandboxDir, 'fakes')], { timeoutMs: 600000 });
  const tag = env.chronosImageTag();
  if (!rebuild && (await imageExists(tag))) return tag;

  let base = env.imageSpec;
  if (base === 'repo') {
    say('building Chronos from the repo Dockerfile (first run takes a few minutes)');
    const r = await docker(['build', '-q', '-t', env.repoBaseImage, '-f', path.join(env.repoDir, 'Dockerfile'), env.repoDir], { timeoutMs: 1800000 });
    if (r.code !== 0) {
      throw new Error(`repo image build failed: ${(r.err || r.out).slice(-1500)}\nretry with --image molo17/gluesync-chronos:<tag> to use a published image`);
    }
    base = env.repoBaseImage;
  } else if (!(await imageExists(base))) {
    say(`pulling ${base}`);
    await mustDocker(['pull', base], { timeoutMs: 900000 });
  }
  say(`building Chronos sandbox overlay ${tag} (libfaketime, py-spy${env.apscheduler ? `, apscheduler==${env.apscheduler}` : ''})`);
  await mustDocker(
    ['build', '-q', '-t', tag, '--build-arg', `BASE_IMAGE=${base}`, '--build-arg', `APSCHEDULER=${env.apscheduler}`, path.join(env.sandboxDir, 'chronos')],
    { timeoutMs: 900000 },
  );
  return tag;
};

// ---------------------------------------------------------------- containers

const containerRunning = async name => {
  const r = await docker(['inspect', '-f', '{{.State.Running}}', name]);
  return r.code === 0 && r.out === 'true';
};

const removeContainer = name => docker(['rm', '-f', name]);

const imageId = async ref => (await docker(['image', 'inspect', '-f', '{{.Id}}', ref])).out;

const startCoreHub = async () => {
  if (await containerRunning(env.corehubContainer)) {
    const running = (await docker(['inspect', '-f', '{{.Image}}', env.corehubContainer])).out;
    if (running === (await imageId(env.fakeImage))) return;
    say('fake Core Hub image changed, recreating the container');
  }
  await removeContainer(env.corehubContainer);
  say('starting fake Core Hub');
  const ports = env.ports;
  await mustDocker([
    'run', '-d', '--name', env.corehubContainer, '--network', env.network, '--network-alias', 'corehub',
    '--label', env.labelArg,
    '-p', `127.0.0.1:${ports.corehub}:1717`,
    '-p', `127.0.0.1:${ports.admin}:1718`,
    '-v', `${env.sharedDir}:/certs:ro`,
    '-e', `FAKE_TLS=${env.tls ? 1 : 0}`,
    '-e', `SANDBOX_ADMIN_TOKEN=${env.adminToken}`,
    '-e', `FAKE_LICENSE=${env.licenseText}`,
    env.fakeImage,
  ]);
  const ready = await waitFor('fake corehub', () => corehub.isUp(), { timeoutMs: 120000, intervalMs: 1000 });
  if (!ready.ok) throw new Error('fake Core Hub did not come up');
};

export const dataVolume = label => `${env.name}-${label}-data`;

export const instances = new Map();

const hostPortOf = async name => {
  for (let i = 0; i < 20; i += 1) {
    const r = await docker(['port', name, `${CHRONOS_PORT}/tcp`]);
    const m = r.out.match(/:(\d+)\s*$/m);
    if (m) return Number(m[1]);
    await new Promise(res => setTimeout(res, 1500));
  }
  throw new Error(`no published port found for ${name}`);
};

// Offset in seconds so that, once the container is ready, the fake clock reads about `clockUtc`.
const offsetFor = clockUtc => Math.round((new Date(clockUtc).getTime() - (Date.now() + leadMs)) / 1000);

const apiUrl = (inst, p) => `${env.scheme}://127.0.0.1:${inst.hostPort}${p}`;

export const fakeNow = inst => Date.now() + (inst.offsetSec || 0) * 1000;

// Starts (or recreates, keeping the data volume) one Chronos container.
//   label     short unique name, used in container, volume and Core Hub module tag
//   tz        process time zone (TZ), default Europe/Rome
//   clockUtc  target fake wall clock at readiness; omit for the real clock
export const startChronos = async ({ label, tz = 'Europe/Rome', clockUtc, env: extraEnv = {}, tag, fixedPort, image, resetData = false }) => {
  const name = label === 'main' ? env.mainContainer : `${env.name}-${label}`;
  const imageTag = image || env.chronosImageTag();
  const moduleTag = tag || (label === 'main' ? 'chronos' : label);
  await removeContainer(name);
  if (resetData) await docker(['volume', 'rm', '-f', dataVolume(label)]);
  await docker(['volume', 'create', '--label', env.labelArg, dataVolume(label)]);

  const offsetSec = clockUtc ? offsetFor(clockUtc) : 0;
  const envVars = {
    SSL_ENABLED: env.tls ? 'true' : 'false',
    SSL_SKIP_VERIFY: 'true',
    GLUESYNC_HOST: 'corehub',
    GLUESYNC_MODULE_TAG: moduleTag,
    TZ: tz,
    ENTITY_START_TIMEOUT: '0',
    CHRONOS_REDO_POLL_INTERVAL: '1',
    LOG_LEVEL: 'INFO',
    PORT: String(CHRONOS_PORT),
    ...(clockUtc
      ? { LD_PRELOAD: '/usr/lib/libfaketime.so.1', FAKETIME: String(offsetSec), FAKETIME_DONT_FAKE_MONOTONIC: '1', FAKETIME_NO_CACHE: '1' }
      : {}),
    ...extraEnv,
  };
  const args = [
    'run', '-d', '--name', name, '--hostname', label, '--network', env.network, '--network-alias', `chronos-${label}`,
    '--label', env.labelArg, '--label', `chronos-sandbox.instance=${label}`,
    '--cap-add', 'SYS_PTRACE',
    '-p', fixedPort ? `127.0.0.1:${fixedPort}:${CHRONOS_PORT}` : `127.0.0.1::${CHRONOS_PORT}`,
    '-v', `${env.sharedDir}:/opt/gluesync/shared:ro`,
    '-v', `${dataVolume(label)}:/app/data`,
    ...Object.entries(envVars).flatMap(([k, v]) => ['-e', `${k}=${v}`]),
    imageTag,
  ];
  const started = Date.now();
  await mustDocker(args, { timeoutMs: 180000 });
  const inst = { label, name, tag: moduleTag, tz, imageTag, offsetSec, clockUtc: clockUtc ? new Date(clockUtc).toISOString() : null, startedAt: nowIso(), hostPort: fixedPort || (await hostPortOf(name)), tokens: {} };
  instances.set(label, inst);
  await corehub.post('/__sandbox/clock', { tag: moduleTag, offsetMs: offsetSec * 1000 });
  inst.startMs = Date.now() - started;
  return inst;
};

export const apiOf = inst => p => apiUrl(inst, p);

export const userToken = async (inst, user = 'admin') => {
  if (inst.tokens[user]) return inst.tokens[user];
  inst.tokens[user] = await corehub.login(user);
  return inst.tokens[user];
};

// True once the REST API answers and Core Hub introspection works (SDK connected).
export const waitReady = async (inst, { timeoutMs = 240000 } = {}) => {
  const began = Date.now();
  const r = await waitFor(
    `${inst.label} ready`,
    async () => {
      const root = await request(apiUrl(inst, '/'), { timeoutMs: 4000 });
      if (root.status === 0) return false;
      const token = await userToken(inst, 'admin');
      const jobs = await request(apiUrl(inst, '/api/jobs/?limit=1'), { headers: { cookie: `gs-auth=${token}` }, timeoutMs: 6000 });
      return jobs.status === 200;
    },
    { timeoutMs, intervalMs: probeIntervalMs },
  );
  if (!r.ok) throw new Error(`Chronos ${inst.label} not ready after ${Math.round((Date.now() - began) / 1000)}s: ${r.last}`);
  inst.readyMs = Date.now() - began + (inst.startMs || 0);
  leadMs = Math.round(0.5 * leadMs + 0.5 * inst.readyMs);
  inst.readyAt = nowIso();
  return inst;
};

export const containerLogs = async (name, { since, until } = {}) => {
  const args = ['logs', '--timestamps'];
  if (since) args.push('--since', since);
  if (until) args.push('--until', until);
  const r = await docker([...args, name], { timeoutMs: 60000 });
  return `${r.out}\n${r.err}`.trim();
};

export const stopChronos = async (inst, { graceSec = 15, remove = true } = {}) => {
  await docker(['stop', '-t', String(graceSec), inst.name], { timeoutMs: (graceSec + 30) * 1000 });
  if (remove) await removeContainer(inst.name);
  inst.stoppedAt = nowIso();
};

export const inspectState = async name => {
  const r = await docker(['inspect', '-f', '{{json .State}}', name]);
  try {
    return JSON.parse(r.out);
  } catch {
    return null;
  }
};

export const containerStats = async name => {
  const r = await docker(['stats', '--no-stream', '--format', '{{json .}}', name], { timeoutMs: 30000 });
  try {
    return JSON.parse(r.out);
  } catch {
    return { error: r.err || r.out };
  }
};

export const pySpyDump = async name => {
  const r = await docker(['exec', name, 'sh', '-c', 'py-spy dump --pid "$(pgrep -f run_scheduler.py | head -n 1)" 2>&1'], { timeoutMs: 60000 });
  return `${r.out}\n${r.err}`.trim();
};

// ---------------------------------------------------------------- lifecycle

const networkExists = async () => (await docker(['network', 'inspect', env.network])).code === 0;

export const up = async ({ rebuild = false } = {}) => {
  fs.mkdirSync(env.runDir, { recursive: true });
  if (env.tls) await prepareShared();
  else {
    fs.mkdirSync(env.sharedDir, { recursive: true });
    fs.writeFileSync(shared('gs-license.dat'), env.licenseText);
  }
  await buildImages({ rebuild });
  if (!(await networkExists())) await mustDocker(['network', 'create', '--label', env.labelArg, env.network]);
  await startCoreHub();

  const running = await containerRunning(env.mainContainer);
  const runningImage = running ? (await docker(['inspect', '-f', '{{.Config.Image}}', env.mainContainer])).out : null;
  if (!running || runningImage !== env.chronosImageTag()) {
    if (running) say(`main Chronos runs ${runningImage}, recreating it as ${env.chronosImageTag()}`);
    say(`starting Chronos (${env.imageSpec}${env.apscheduler ? `, apscheduler ${env.apscheduler}` : ''}, TZ Europe/Rome)`);
    const inst = await startChronos({ label: 'main', fixedPort: env.ports.chronos, resetData: running });
    await waitReady(inst);
  } else {
    const inst = await adoptMain();
    await waitReady(inst);
  }
  say(`sandbox up: Chronos ${env.scheme}://127.0.0.1:${env.ports.chronos}, fake Core Hub ${env.scheme}://127.0.0.1:${env.ports.corehub}, admin http://127.0.0.1:${env.ports.admin}`);
};

export const adoptMain = async () => {
  if (instances.has('main')) return instances.get('main');
  const inst = { label: 'main', name: env.mainContainer, tag: 'chronos', tz: 'Europe/Rome', imageTag: env.chronosImageTag(), offsetSec: 0, hostPort: env.ports.chronos, tokens: {} };
  instances.set('main', inst);
  return inst;
};

export const down = async ({ purge = false } = {}) => {
  const ids = await docker(['ps', '-aq', '--filter', `label=${env.labelArg}`]);
  if (ids.out) await docker(['rm', '-f', ...ids.out.split('\n')], { timeoutMs: 180000 });
  const vols = await docker(['volume', 'ls', '-q', '--filter', `label=${env.labelArg}`]);
  if (vols.out) await docker(['volume', 'rm', '-f', ...vols.out.split('\n')]);
  await docker(['network', 'rm', env.network]);
  say(`removed containers, volumes and network of ${env.name}`);
  if (purge) {
    fs.rmSync(env.runDir, { recursive: true, force: true });
    const others = await docker(['ps', '-aq', '--filter', 'label=chronos-sandbox']);
    if (others.out) say('removed run/; images kept because another sandbox project still has containers (reports are kept)');
    else {
      const imgs = await docker(['images', '-q', 'chronos-sandbox/*']);
      if (imgs.out) await docker(['rmi', '-f', ...new Set(imgs.out.split('\n'))]);
      say('removed run/ and sandbox images (reports are kept)');
    }
  }
};

export const status = async () => {
  const r = await docker(['ps', '-a', '--filter', `label=${env.labelArg}`, '--format', '{{.Names}}\t{{.Status}}\t{{.Ports}}']);
  say(`sandbox ${env.name}`);
  process.stdout.write(`${r.out || '(no containers)'}\n`);
  if (await corehub.isUp().catch(() => false)) {
    const st = await corehub.state();
    say('fake Core Hub', { up: st.up, stats: st.stats, modules: st.modules.map(m => ({ tag: m.tag, open: m.open, pings: m.pings, lastPingAt: m.lastPingAt })), faults: st.faults.length });
  }
};

