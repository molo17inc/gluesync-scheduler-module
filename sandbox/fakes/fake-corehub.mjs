// Fake Gluesync Core Hub for the Chronos sandbox.
//
// Everything Chronos calls on Core Hub lives here, backed by an in-memory model
// (pipelines, entities, groups, maintenance flag, running state), so commands have
// realistic effects. Two listeners:
//
//   :1717  Core Hub API + WSS /ext-module   (HTTPS unless FAKE_TLS=0)
//   :1718  sandbox admin API, plain HTTP    (x-sandbox-admin header)
//
// The admin API is separate so it keeps answering while the Core Hub listener is
// "down". Every request and every WebSocket event lands in a call log with real UTC
// time (plus the Chronos fake-clock time when the orchestrator registered an offset
// for that module tag).

import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { WebSocketServer } from 'ws';

const PORT = Number(process.env.FAKE_PORT || 1717);
const ADMIN_PORT = Number(process.env.FAKE_ADMIN_PORT || 1718);
const TLS = process.env.FAKE_TLS !== '0';
const ADMIN_TOKEN = process.env.SANDBOX_ADMIN_TOKEN || 'sandbox-admin';
const EXPECTED_LICENSE = process.env.FAKE_LICENSE || '';
const LOG_CAP = 60000;

const sleep = ms => new Promise(r => setTimeout(r, ms));
const b64 = v => Buffer.from(JSON.stringify(v)).toString('base64url');

const USERS = {
  admin: { password: 'admin', role: 'SUPER_ADMIN' },
  manager: { password: 'manager', role: 'MANAGER' },
  monitor: { password: 'monitor', role: 'MONITOR' },
  viewer: { password: 'viewer', role: 'VIEWER' },
};

// ---------------------------------------------------------------- model

const seedPipelines = () => {
  const make = (id, name) => ({
    id,
    name,
    maintenance: false,
    entities: ['e1', 'e2', 'e3', 'e4'].map((n, i) => ({
      id: `${id}-${n}`,
      name: `${name} table ${i + 1}`,
      groupId: i < 2 ? 'grp-a' : 'grp-b',
      active: true,
      migrating: false,
      error: null,
    })),
    groups: [
      { id: 'grp-a', name: 'Group A' },
      { id: 'grp-b', name: 'Group B' },
    ],
    prevActive: null,
  });
  return Object.fromEntries(
    [
      ['pl-orders', 'Orders'],
      ['pl-inventory', 'Inventory'],
      ['pl-maint-a', 'Maintenance A'],
      ['pl-maint-b', 'Maintenance B'],
      ['pl-extra', 'Extra'],
    ].map(([id, name]) => [id, make(id, name)]),
  );
};

const state = {
  pipelines: seedPipelines(),
  sessions: new Map(),
  moduleTokens: new Map(),
  modules: new Map(),
  webhooks: new Map(),
  faults: [],
  clock: new Map(),
  log: [],
  seq: 0,
  up: true,
  wsReject: new Set(),
  webhooksDisabled: false,
  seqCounters: { runs: 0, validations: 0 },
  aiRuns: new Map(),
  validations: new Map(),
  brain: { status: 'READY', generation: 1, message: 'Enterprise brain is ready.', tablesSeen: 12, edgesSeen: 9, creatingUntil: 0, fail: false },
  vizFlaky: new Map(),
  stats: { handshakes: 0, pings: 0, rejected: 0 },
};

const cloneAutoPipeline = id => {
  const p = seedPipelines()['pl-extra'];
  p.id = id;
  p.name = id;
  p.entities.forEach((e, i) => {
    e.id = `${id}-e${i + 1}`;
  });
  state.pipelines[id] = p;
  return p;
};

const pipelineOf = id => state.pipelines[id] || (id.startsWith('auto-') ? cloneAutoPipeline(id) : null);

// ---------------------------------------------------------------- logging

const record = entry => {
  state.seq += 1;
  const now = Date.now();
  const e = { seq: state.seq, ts: new Date(now).toISOString(), ...entry };
  const offset = e.tag ? state.clock.get(e.tag) : undefined;
  if (offset !== undefined) e.fakeTs = new Date(now + offset).toISOString();
  state.log.push(e);
  if (state.log.length > LOG_CAP) state.log.splice(0, state.log.length - LOG_CAP);
  return e;
};

const trim = (v, n) => {
  if (v === undefined) return undefined;
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return s.length > n ? `${s.slice(0, n)}...` : s;
};

// ---------------------------------------------------------------- http helpers

const readBody = req =>
  new Promise(resolve => {
    const chunks = [];
    req.on('data', c => chunks.push(c));
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      if (!raw) return resolve(undefined);
      try {
        resolve(JSON.parse(raw));
      } catch {
        resolve({ __raw: raw });
      }
    });
    req.on('error', () => resolve(undefined));
  });

const json = (status, body, headers = {}) => ({ status, body, headers });
const text = (status, body, headers = {}) => ({ status, body: String(body), headers: { 'content-type': 'text/plain', ...headers } });

const send = (res, r) => {
  const isText = typeof r.body === 'string';
  const payload = r.body === undefined ? '' : isText ? r.body : JSON.stringify(r.body);
  res.writeHead(r.status, { 'content-type': isText ? 'text/plain' : 'application/json', ...r.headers });
  res.end(payload);
};

const cookieToken = req => {
  const cookie = req.headers.cookie || '';
  const m = cookie.split(';').map(s => s.trim()).find(s => s.startsWith('gs-auth='));
  return m ? m.slice('gs-auth='.length) : null;
};

const tokenOf = req => {
  const authz = req.headers.authorization;
  if (authz) return authz.replace(/^Bearer\s+/i, '').trim();
  return cookieToken(req);
};

const identify = req => {
  const token = tokenOf(req);
  if (!token) return null;
  const mod = state.moduleTokens.get(token);
  if (mod && !mod.revoked) return { type: 'module', tag: mod.tag, username: mod.tag, role: 'EXTERNAL_MODULE' };
  const s = state.sessions.get(token);
  if (s && !s.revoked && (!s.expiresAt || Date.now() < s.expiresAt)) return { type: 'user', username: s.username, role: s.role };
  return null;
};

// ---------------------------------------------------------------- faults

const matchFault = (f, req, path, tag) => {
  if (f.until && Date.now() > f.until) return false;
  if (f.count !== null && f.count !== undefined && f.count <= 0) return false;
  const m = f.match || {};
  if (m.method && m.method.toUpperCase() !== req.method) return false;
  if (m.tag && m.tag !== tag) return false;
  if (m.path) {
    if (m.path.startsWith('re:')) {
      if (!new RegExp(m.path.slice(3)).test(path)) return false;
    } else if (!path.startsWith(m.path)) return false;
  }
  return true;
};

const takeFault = (req, path, tag) => {
  const f = state.faults.find(x => x.mode !== 'maintenance406' && matchFault(x, req, path, tag));
  if (!f) return null;
  if (f.count !== null && f.count !== undefined) f.count -= 1;
  f.hits = (f.hits || 0) + 1;
  return f;
};

// ---------------------------------------------------------------- webhooks

const knownEvents = {
  start: 'ENTITY_CDC_STARTED',
  stop: 'ENTITY_CDC_STOPPED',
  'one-time-snapshot': 'ENTITY_SNAPSHOT_COMPLETED',
  redo: 'ENTITY_SNAPSHOT_COMPLETED',
  'start-group': 'GROUP_CDC_STARTED',
  'stop-group': 'GROUP_CDC_STOPPED',
  'one-time-snapshot-group': 'GROUP_SNAPSHOT_COMPLETED',
  'redo-group': 'GROUP_SNAPSHOT_COMPLETED',
};

const moduleFor = tag => [...state.modules.values()].filter(m => m.tag === tag && m.open).pop();

const deliver = async (tag, hook, payload) => {
  const mod = moduleFor(tag);
  if (!mod) {
    record({ kind: 'webhook', event: 'undeliverable', tag, hook: hook.id, note: 'module not connected' });
    return;
  }
  const base = `${mod.scheme}://${mod.remote}:${mod.port}`;
  const target = String(hook.webhookUrl || '').replace('{{chronos_address}}', base);
  let status = 0;
  let body;
  try {
    const url = new URL(target);
    const lib = url.protocol === 'https:' ? https : http;
    const data = JSON.stringify(payload);
    status = await new Promise(resolve => {
      const r = lib.request(
        url,
        {
          method: 'POST',
          rejectUnauthorized: false,
          timeout: 10000,
          headers: { 'content-type': 'application/json', 'content-length': Buffer.byteLength(data), ...(hook.customHeaders || {}) },
        },
        res => {
          const chunks = [];
          res.on('data', c => chunks.push(c));
          res.on('end', () => {
            body = Buffer.concat(chunks).toString('utf8');
            resolve(res.statusCode);
          });
        },
      );
      r.on('error', e => {
        body = String(e.message);
        resolve(0);
      });
      r.on('timeout', () => r.destroy(new Error('timeout')));
      r.end(data);
    });
  } catch (e) {
    body = String(e.message);
  }
  record({ kind: 'webhook', event: payload.event?.type, tag, hook: hook.id, url: target, status, res: trim(body, 200), headers: hook.customHeaders });
};

const emit = (eventType, { pipelineId, entityId, groupId, tag, source } = {}) => {
  if (state.webhooksDisabled) return;
  const owners = tag ? [tag] : [...state.webhooks.keys()];
  owners.forEach(owner => {
    (state.webhooks.get(owner) || []).forEach(hook => {
      if (hook.enabled === false) return;
      if (!(hook.enabledEvents || []).includes(eventType)) return;
      if ((hook.pipelineFilter || []).length && !hook.pipelineFilter.includes(pipelineId)) return;
      if ((hook.entityFilter || []).length && !hook.entityFilter.includes(entityId)) return;
      if ((hook.groupFilter || []).length && !hook.groupFilter.includes(groupId)) return;
      const payload = {
        event: {
          type: eventType,
          pipelineId,
          entityId,
          groupId,
          source: source || (entityId ? { kind: 'entity', id: entityId } : groupId ? { kind: 'group', id: groupId } : { kind: 'pipeline', id: pipelineId }),
        },
        user: 'sandbox',
      };
      setTimeout(() => deliver(owner, hook, payload), 250);
    });
  });
};

// ---------------------------------------------------------------- core hub routes

const inMaintenance = id => `Pipeline with id ${id} is in maintenance. Please wait until the maintenance mode is disabled.`;

const selectEntities = (p, q) => {
  if (q.get('entity')) {
    const e = p.entities.find(x => x.id === q.get('entity'));
    return e ? [e] : null;
  }
  if (q.get('groupId')) {
    if (!p.groups.some(g => g.id === q.get('groupId'))) return null;
    return p.entities.filter(x => x.groupId === q.get('groupId'));
  }
  return p.entities;
};

const syncCommand = (p, action, q, tag) => {
  const targets = selectEntities(p, q);
  if (!targets) return json(404, { message: q.get('entity') ? `Entity ${q.get('entity')} not found` : `Group ${q.get('groupId')} not found` });
  const groupId = q.get('groupId') || undefined;
  const event = knownEvents[action];
  if (action === 'start' || action === 'start-group') {
    targets.forEach(e => {
      e.active = true;
    });
  } else if (action === 'stop' || action === 'stop-group') {
    targets.forEach(e => {
      e.active = false;
    });
  } else {
    targets.forEach(e => {
      e.migrating = true;
      setTimeout(() => {
        e.migrating = false;
      }, 400);
    });
  }
  if (action.endsWith('group')) emit(event, { pipelineId: p.id, groupId, tag });
  else targets.forEach(e => emit(event, { pipelineId: p.id, entityId: e.id, groupId: e.groupId, tag }));
  return json(200, { status: 'OK', message: `${action} accepted for ${targets.length} entit${targets.length === 1 ? 'y' : 'ies'}` });
};

const maintenanceCommand = (p, action, tag) => {
  if (action === 'enter') {
    if (!p.maintenance) {
      p.maintenance = true;
      p.prevActive = Object.fromEntries(p.entities.map(e => [e.id, e.active]));
      p.entities.forEach(e => {
        e.active = false;
      });
    }
    emit('PIPELINE_ENTER_MAINTENANCE', { pipelineId: p.id, tag });
    return json(200, { status: 'OK', message: `Pipeline ${p.id} entered maintenance` });
  }
  if (p.maintenance) {
    p.maintenance = false;
    p.entities.forEach(e => {
      e.active = p.prevActive?.[e.id] ?? true;
    });
    p.prevActive = null;
  }
  emit('PIPELINE_EXIT_MAINTENANCE', { pipelineId: p.id, tag });
  return json(200, { status: 'OK', message: `Pipeline ${p.id} left maintenance` });
};

const entityJson = e => ({ id: e.id, entityId: e.id, name: e.name, groupId: e.groupId });
const statusJson = e => ({
  entityId: e.id,
  isSyncActive: e.active,
  isMigrationActive: e.migrating,
  isBusy: e.migrating,
  errorState: e.error || null,
});

const savedQueries = {
  'sq-orders': { id: 'sq-orders', sql: 'SELECT count(*) FROM orders', agentId: 'agent-pg' },
};

const nextRunId = () => `run-${(state.seqCounters.runs += 1)}`;

const coreHub = async (req, path, q, body, who) => {
  const m = req.method;
  const seg = path.split('/').filter(Boolean);

  if (path === '/health' || path === '/version') return json(200, { status: 'ok', version: 'sandbox' });

  if (path === '/authentication/login' && m === 'POST') {
    const user = USERS[body?.username];
    if (!user || user.password !== body?.password) return json(401, { message: 'Invalid credentials' });
    const token = `sess.${b64({ sub: body.username, role: user.role })}.${randomBytes(8).toString('hex')}`;
    state.sessions.set(token, { username: body.username, role: user.role, expiresAt: Date.now() + Number(body.ttlSeconds || 86400) * 1000 });
    return json(200, { token, username: body.username, role: user.role }, { 'set-cookie': `gs-auth=${token}; Path=/; HttpOnly` });
  }
  if (path === '/authentication/logout' && m === 'POST') {
    const t = tokenOf(req);
    if (t && state.sessions.get(t)) state.sessions.get(t).revoked = true;
    return json(200, { success: true });
  }
  if (path === '/authentication/me' && m === 'GET') {
    if (!who) return json(401, { message: 'Not authenticated' });
    return json(200, { username: who.username, role: who.role });
  }

  if (!who) return json(401, { message: 'Unauthorized' });
  const tag = who.type === 'module' ? who.tag : undefined;

  if (path === '/global-config/webhooks') {
    if (!tag) return json(403, { message: 'module token required' });
    if (m === 'GET') return json(200, state.webhooks.get(tag) || []);
    if (m === 'PUT') {
      if (!Array.isArray(body)) return json(400, { message: 'array expected' });
      state.webhooks.set(tag, body);
      return json(200, body);
    }
  }

  if (path === '/agents' && m === 'GET') return json(200, [{ id: 'agent-pg', name: 'Postgres source' }, { id: 'agent-mysql', name: 'MySQL target' }]);
  if (path === '/settings' && m === 'GET') return json(200, { sandbox: true });

  if (path === '/pipelines' && m === 'GET') {
    return json(200, { pipelines: Object.values(state.pipelines).map(p => ({ id: p.id, name: p.name, inMaintenance: p.maintenance })) });
  }

  if (seg[0] === 'pipelines' && seg.length >= 2) {
    const p = pipelineOf(seg[1]);
    if (!p) return json(404, { message: `Pipeline with id ${seg[1]} not found` });
    const rest = seg.slice(2).join('/');

    if (rest === '' && m === 'GET') return json(200, { id: p.id, name: p.name, inMaintenance: p.maintenance, entities: p.entities.length });
    if (rest === 'entities' && m === 'GET') return json(200, { entities: p.entities.map(entityJson) });
    if (seg[2] === 'entities' && seg.length === 4 && m === 'GET') {
      const e = p.entities.find(x => x.id === seg[3]);
      return e ? json(200, entityJson(e)) : json(404, { message: `Entity ${seg[3]} not found` });
    }
    if (rest === 'entities-status' && m === 'GET') return json(200, p.entities.map(statusJson));
    if (rest === 'config/entities' && m === 'GET') return json(200, p.entities.map(entityJson));
    if (rest === 'config/groups' && m === 'GET') return json(200, p.groups);

    if (seg[2] === 'commands' && m === 'POST') {
      const kind = seg[3];
      const action = seg[4];
      const forced = state.faults.find(f => f.mode === 'maintenance406' && matchFault(f, req, path, tag) && (!f.pipeline || f.pipeline === p.id));
      if (kind === 'sync') {
        if (p.maintenance || forced) {
          if (forced) {
            forced.hits = (forced.hits || 0) + 1;
            if (forced.count !== null && forced.count !== undefined) forced.count -= 1;
          }
          return text(406, inMaintenance(p.id));
        }
        if (!knownEvents[action]) return json(404, { message: `unknown sync command ${action}` });
        return syncCommand(p, action, q, tag);
      }
      if (kind === 'maintenance' && (action === 'enter' || action === 'exit')) return maintenanceCommand(p, action, tag);
    }
  }

  if (path === '/query-studio/agents' && m === 'GET') {
    return json(200, [
      { id: 'agent-pg', name: 'Postgres source', type: 'SOURCE', pipelineId: 'pl-orders' },
      { id: 'agent-mysql', name: 'MySQL target', type: 'TARGET', pipelineId: 'pl-orders' },
    ]);
  }
  if (seg[0] === 'query-studio' && seg[1] === 'saved-queries' && m === 'GET') {
    const sq = savedQueries[seg[2]];
    return sq ? json(200, sq) : json(404, { message: `Saved query ${seg[2]} not found` });
  }
  if (seg[0] === 'query-studio' && seg[1] === 'pipelines' && seg[3] === 'agents' && seg[5] === 'execute' && m === 'POST') {
    if (!pipelineOf(seg[2])) return json(404, { message: `Pipeline with id ${seg[2]} not found` });
    const sql = String(body?.sql || '');
    const readOnly = body?.options?.readOnly !== false;
    if (readOnly && /^\s*(insert|update|delete|drop|truncate|alter)\b/i.test(sql)) {
      return json(403, { message: 'Read-only query rejected: write statements require options.readOnly=false' });
    }
    if (/SANDBOX_FAIL/.test(sql)) return json(200, { status: 'ERROR', message: 'query failed on agent' });
    return json(200, { status: 'OK', columns: ['count'], rows: [[42]], readOnly });
  }

  if (seg[0] === 'api' && seg[1] === 'ai' && seg[2] === 'v1') {
    if (seg[3] === 'agents' && seg[5] === 'runs' && m === 'POST') {
      const slug = decodeURIComponent(seg[4]);
      if (slug === 'unknown-agent') return json(404, { message: `Agent ${slug} not found` });
      const id = nextRunId();
      state.aiRuns.set(id, { id, slug, polls: 0, input: body?.input, version: body?.agentVersion, idempotencyKey: body?.idempotencyKey });
      return json(202, { id, status: 'RUNNING' });
    }
    if (seg[3] === 'runs' && m === 'GET') {
      const r = state.aiRuns.get(seg[4]);
      if (!r) return json(404, { message: `Run ${seg[4]} not found` });
      r.polls += 1;
      if (r.polls < 3) return json(200, { id: r.id, status: r.slug === 'approval-agent' && r.polls === 2 ? 'WAITING_APPROVAL' : 'RUNNING' });
      return json(200, { id: r.id, status: r.slug === 'failing-agent' ? 'FAILED' : 'SUCCEEDED' });
    }
    if (seg[3] === 'brain' && seg[4] === 'job') {
      const b = state.brain;
      if (m === 'POST') {
        b.generation += 1;
        b.status = 'CREATING';
        b.creatingUntil = Date.now() + 3000;
        b.message = 'Rebuilding';
        return json(202, { status: 'CREATING', generation: b.generation });
      }
      if (m === 'GET') {
        if (b.status === 'CREATING' && Date.now() > b.creatingUntil) {
          b.status = b.fail ? 'FAILED' : 'READY';
          b.message = b.status === 'READY' ? 'Enterprise brain is ready.' : 'Brain rebuild failed';
        }
        return json(200, { status: b.status, generation: b.generation, message: b.message, tablesSeen: b.tablesSeen, edgesSeen: b.edgesSeen });
      }
    }
  }

  if (seg[0] === 'data-comparison' && seg[1] === 'runs') {
    if (seg.length === 2 && m === 'POST') {
      const id = `val-${(state.seqCounters.validations += 1)}`;
      const diffs = String(body?.entityId || '').includes('diff');
      state.validations.set(id, { id, entityId: body?.entityId, diffs, polls: 0, reconciled: false });
      return json(202, { jobId: id });
    }
    const v = state.validations.get(seg[2]);
    if (!v) return json(404, { message: `Run ${seg[2]} not found` });
    if (seg.length === 3 && m === 'GET') {
      v.polls += 1;
      if (v.polls < 2) return json(200, { jobId: v.id, status: 'RUNNING' });
      const failing = String(v.entityId).includes('broken');
      return json(200, {
        jobId: v.id,
        status: failing ? 'FAILED' : 'COMPLETED',
        errorMessage: failing ? 'comparison engine crashed' : null,
        sourceRowsScanned: 1000,
        targetRowsScanned: v.diffs ? 997 : 1000,
        missingInTargetCount: v.diffs ? 3 : 0,
        missingInSourceCount: 0,
        rowMismatchesCount: 0,
        schemaDifferencesCount: 0,
      });
    }
    if (seg[3] === 'reconciliation' && m === 'POST') {
      v.reconciled = true;
      return json(200, { insertedCount: 3, deletedCount: 0, updatedCount: 0, skippedCount: 0 });
    }
  }

  if (path === '/visualize/refresh' && m === 'POST') {
    const viz = String(body?.vizId || '');
    if (viz === 'viz-gone') return json(404, { message: 'Visualization not found' });
    if (viz === 'viz-bad') return json(400, { message: 'Invalid parameters for visualization' });
    if (viz === 'viz-flaky') {
      const n = (state.vizFlaky.get(viz) || 0) + 1;
      state.vizFlaky.set(viz, n);
      if (n <= 2) return json(503, { message: 'Visualize backend busy' });
    }
    return json(202, { scheduled: true, vizId: viz });
  }

  return json(404, { message: `no route ${m} ${path}` });
};

// ---------------------------------------------------------------- core hub server

const handleCoreHub = async (req, res) => {
  const started = Date.now();
  const url = new URL(req.url, 'http://corehub');
  const path = url.pathname;
  const body = await readBody(req);
  const who = identify(req);
  const tag = who?.type === 'module' ? who.tag : undefined;
  const entry = { kind: 'http', method: req.method, path, query: Object.fromEntries(url.searchParams), tag, user: who?.type === 'user' ? who.username : undefined, req: trim(body, 1500) };
  const finish = (status, extra = {}) => record({ ...entry, status, ms: Date.now() - started, ...extra });

  try {
    let fault = takeFault(req, path, tag);
    if (fault && fault.mode === 'slow') {
      await sleep(fault.delayMs || 5000);
      entry.fault = 'slow';
      fault = null;
    }
    if (fault) {
      if (fault.mode === 'drop') {
        finish(0, { fault: 'drop' });
        req.socket.destroy();
        return;
      }
      if (fault.mode === 'status') {
        const status = fault.status || 503;
        const r = json(status, { message: fault.message || `sandbox fault ${status}` });
        finish(status, { fault: `status:${status}`, res: trim(r.body, 200) });
        return send(res, r);
      }
      if (fault.mode === 'auth401' && !path.startsWith('/authentication/login')) {
        const r = json(401, { message: 'Token rejected (sandbox fault)' });
        finish(401, { fault: 'auth401', res: trim(r.body, 200) });
        return send(res, r);
      }
    }
    const r = await coreHub(req, path, url.searchParams, body, who);
    finish(r.status, { res: trim(r.body, 200) });
    return send(res, r);
  } catch (e) {
    finish(500, { error: String(e.stack || e) });
    return send(res, json(500, { message: String(e) }));
  }
};

const sockets = new Set();
const tlsOpts = () => ({
  cert: fs.readFileSync(process.env.FAKE_TLS_CERT || '/certs/server.pem'),
  key: fs.readFileSync(process.env.FAKE_TLS_KEY || '/certs/server-key.pem'),
});
const mainServer = TLS ? https.createServer(tlsOpts(), handleCoreHub) : http.createServer(handleCoreHub);
mainServer.on('connection', s => {
  sockets.add(s);
  s.on('close', () => sockets.delete(s));
});
mainServer.on('secureConnection', s => {
  sockets.add(s);
  s.on('close', () => sockets.delete(s));
});

// ---------------------------------------------------------------- websocket

const wss = new WebSocketServer({ noServer: true });
mainServer.on('upgrade', (req, socket, head) => {
  const url = new URL(req.url, 'http://corehub');
  if (url.pathname !== '/ext-module') {
    socket.destroy();
    return;
  }
  wss.handleUpgrade(req, socket, head, ws => onModule(ws, req));
});

const onModule = (ws, req) => {
  const license = req.headers['module-license'];
  const tag = req.headers['module-tag'] || 'unknown';
  const id = randomUUID().slice(0, 8);
  const remote = (req.socket.remoteAddress || '').replace(/^::ffff:/, '');
  const info = {
    id,
    tag,
    remote,
    port: Number(req.headers['module-port'] || 1717),
    scheme: req.headers['module-scheme'] || 'http',
    connectedAt: new Date().toISOString(),
    pings: 0,
    lastPingAt: null,
    open: true,
    ws,
  };
  state.modules.set(id, info);
  record({ kind: 'ws', event: 'connect', tag, conn: id, remote, port: info.port, scheme: info.scheme });

  const reject = (code, reason) => {
    state.stats.rejected += 1;
    record({ kind: 'ws', event: 'reject', tag, conn: id, code, reason });
    info.open = false;
    ws.close(code, reason);
  };
  if (!license) return reject(1008, 'NOT_CONSISTENT: Missing module license in header. Please try again.');
  if (state.wsReject.has('*') || state.wsReject.has(tag)) return reject(1008, 'VIOLATED_POLICY: License not valid or expired');
  if (EXPECTED_LICENSE && license !== EXPECTED_LICENSE) return reject(1008, 'VIOLATED_POLICY: License not valid or expired');

  let shook = false;
  ws.on('message', data => {
    const msg = data.toString();
    if (!shook) {
      shook = true;
      state.stats.handshakes += 1;
      const now = Math.floor(Date.now() / 1000);
      const token = `mod.${b64({ sub: tag, role: 'EXTERNAL_MODULE', iat: now, exp: now + 3600 })}.${createHash('sha1').update(randomBytes(8)).digest('hex').slice(0, 16)}`;
      state.moduleTokens.set(token, { tag, issuedAt: Date.now(), revoked: false });
      info.token = token;
      record({ kind: 'ws', event: 'handshake', tag, conn: id, first: msg.slice(0, 20) });
      ws.send(token);
      return;
    }
    if (msg === 'ping') {
      info.pings += 1;
      info.lastPingAt = new Date().toISOString();
      state.stats.pings += 1;
      record({ kind: 'ws', event: 'ping', tag, conn: id });
      ws.send('pong');
    }
  });
  ws.on('close', (code, reason) => {
    info.open = false;
    record({ kind: 'ws', event: 'close', tag, conn: id, code, reason: reason?.toString() });
  });
  ws.on('error', e => record({ kind: 'ws', event: 'error', tag, conn: id, error: String(e.message) }));
};

// ---------------------------------------------------------------- up / down

let listening = false;
const goUp = () =>
  new Promise(resolve => {
    if (listening) return resolve();
    mainServer.listen(PORT, '0.0.0.0', () => {
      listening = true;
      state.up = true;
      record({ kind: 'sandbox', event: 'up' });
      resolve();
    });
  });

const goDown = () =>
  new Promise(resolve => {
    if (!listening) return resolve();
    listening = false;
    state.up = false;
    record({ kind: 'sandbox', event: 'down' });
    mainServer.close(() => resolve());
    state.modules.forEach(m => m.ws.terminate());
    sockets.forEach(s => s.destroy());
  });

// ---------------------------------------------------------------- admin

const adminServer = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://admin');
  const path = url.pathname;
  const q = url.searchParams;
  if (req.headers['x-sandbox-admin'] !== ADMIN_TOKEN && path !== '/ready') return send(res, json(403, { error: 'admin token required' }));
  const body = await readBody(req);
  try {
    if (path === '/ready') return send(res, json(200, { up: state.up, seq: state.seq }));
    if (path === '/__sandbox/state') {
      return send(
        res,
        json(200, {
          up: state.up,
          stats: state.stats,
          seq: state.seq,
          pipelines: Object.values(state.pipelines).map(p => ({
            id: p.id,
            maintenance: p.maintenance,
            entities: p.entities.map(e => ({ id: e.id, groupId: e.groupId, active: e.active, migrating: e.migrating, error: e.error })),
          })),
          modules: [...state.modules.values()].map(({ ws, ...m }) => m),
          webhooks: Object.fromEntries(state.webhooks),
          faults: state.faults,
          wsReject: [...state.wsReject],
          clock: Object.fromEntries(state.clock),
          tokens: [...state.moduleTokens.values()].filter(t => !t.revoked).length,
        }),
      );
    }
    if (path === '/__sandbox/log' && req.method === 'GET') {
      const since = Number(q.get('since') || 0);
      const limit = Number(q.get('limit') || 20000);
      let items = state.log.filter(e => e.seq > since);
      if (q.get('tag')) items = items.filter(e => e.tag === q.get('tag'));
      if (q.get('kind')) items = items.filter(e => e.kind === q.get('kind'));
      return send(res, json(200, { lastSeq: state.seq, items: items.slice(-limit) }));
    }
    if (path === '/__sandbox/log/clear' && req.method === 'POST') {
      state.log.length = 0;
      return send(res, json(200, { cleared: true, seq: state.seq }));
    }
    if (path === '/__sandbox/faults') {
      if (req.method === 'POST') {
        const f = { id: body.id || randomUUID().slice(0, 6), mode: body.mode, match: body.match || {}, status: body.status, message: body.message, delayMs: body.delayMs, count: body.count ?? null, pipeline: body.pipeline, until: body.forMs ? Date.now() + body.forMs : null, hits: 0 };
        state.faults.push(f);
        record({ kind: 'sandbox', event: 'fault-add', fault: f });
        return send(res, json(200, f));
      }
      if (req.method === 'DELETE') {
        const id = q.get('id');
        state.faults = id ? state.faults.filter(f => f.id !== id) : [];
        record({ kind: 'sandbox', event: 'fault-clear', id });
        return send(res, json(200, { faults: state.faults }));
      }
      return send(res, json(200, state.faults));
    }
    if (path === '/__sandbox/down' && req.method === 'POST') {
      await goDown();
      if (body?.forMs) setTimeout(goUp, body.forMs);
      return send(res, json(200, { up: false, forMs: body?.forMs || null }));
    }
    if (path === '/__sandbox/up' && req.method === 'POST') {
      await goUp();
      return send(res, json(200, { up: true }));
    }
    if (path === '/__sandbox/ws-drop' && req.method === 'POST') {
      let n = 0;
      state.modules.forEach(m => {
        if (!m.open || (body?.tag && m.tag !== body.tag)) return;
        n += 1;
        if (body?.mode === 'close') m.ws.close(1011, 'sandbox close');
        else m.ws.terminate();
      });
      record({ kind: 'sandbox', event: 'ws-drop', count: n, mode: body?.mode || 'terminate', tag: body?.tag });
      return send(res, json(200, { dropped: n }));
    }
    if (path === '/__sandbox/ws-reject' && req.method === 'POST') {
      const key = body?.tag || '*';
      if (body?.on) state.wsReject.add(key);
      else state.wsReject.delete(key);
      record({ kind: 'sandbox', event: 'ws-reject', on: !!body?.on, tag: key });
      return send(res, json(200, { wsReject: [...state.wsReject] }));
    }
    if (path === '/__sandbox/revoke-tokens' && req.method === 'POST') {
      let n = 0;
      state.moduleTokens.forEach(t => {
        if (!body?.tag || t.tag === body.tag) {
          t.revoked = true;
          n += 1;
        }
      });
      record({ kind: 'sandbox', event: 'revoke-tokens', count: n, tag: body?.tag });
      return send(res, json(200, { revoked: n }));
    }
    if (path === '/__sandbox/clock' && req.method === 'POST') {
      if (body.offsetMs === null) state.clock.delete(body.tag);
      else state.clock.set(body.tag, Number(body.offsetMs));
      return send(res, json(200, { clock: Object.fromEntries(state.clock) }));
    }
    if (path === '/__sandbox/reset' && req.method === 'POST') {
      state.pipelines = seedPipelines();
      state.webhooks = new Map();
      state.faults = [];
      state.wsReject = new Set();
      state.webhooksDisabled = false;
      state.aiRuns = new Map();
      state.validations = new Map();
      state.vizFlaky = new Map();
      state.brain = { ...state.brain, status: 'READY', creatingUntil: 0, fail: false };
      record({ kind: 'sandbox', event: 'reset' });
      return send(res, json(200, { reset: true }));
    }
    if (path === '/__sandbox/webhooks' && req.method === 'POST') {
      state.webhooksDisabled = body?.enabled === false;
      record({ kind: 'sandbox', event: 'webhook-delivery', enabled: !state.webhooksDisabled });
      return send(res, json(200, { enabled: !state.webhooksDisabled }));
    }
    if (path === '/__sandbox/brain' && req.method === 'POST') {
      state.brain.fail = !!body?.fail;
      return send(res, json(200, { fail: state.brain.fail }));
    }
    if (path === '/__sandbox/pipelines' && req.method === 'POST') {
      const p = pipelineOf(body.id) || cloneAutoPipeline(body.id);
      return send(res, json(200, { id: p.id }));
    }
    if (path.startsWith('/__sandbox/pipelines/') && path.endsWith('/maintenance') && req.method === 'POST') {
      const p = pipelineOf(path.split('/')[3]);
      if (!p) return send(res, json(404, { error: 'unknown pipeline' }));
      if (!!body?.on !== p.maintenance) maintenanceCommand(p, body?.on ? 'enter' : 'exit', undefined);
      return send(res, json(200, { id: p.id, maintenance: p.maintenance }));
    }
    if (path.startsWith('/__sandbox/entities/') && req.method === 'POST') {
      const [, , , pid, eid] = path.split('/');
      const e = pipelineOf(pid)?.entities.find(x => x.id === eid);
      if (!e) return send(res, json(404, { error: 'unknown entity' }));
      Object.assign(e, body);
      return send(res, json(200, e));
    }
    if (path === '/__sandbox/emit' && req.method === 'POST') {
      emit(body.event, { pipelineId: body.pipelineId, entityId: body.entityId, groupId: body.groupId, tag: body.tag, source: body.source });
      record({ kind: 'sandbox', event: 'emit', type: body.event, pipelineId: body.pipelineId, entityId: body.entityId, tag: body.tag });
      return send(res, json(200, { emitted: body.event }));
    }
    return send(res, json(404, { error: `no admin route ${req.method} ${path}` }));
  } catch (e) {
    return send(res, json(500, { error: String(e.stack || e) }));
  }
});

await goUp();
adminServer.listen(ADMIN_PORT, '0.0.0.0', () => {
  fs.writeFileSync('/tmp/ready', new Date().toISOString());
  process.stdout.write(`${JSON.stringify({ ts: new Date().toISOString(), msg: 'fake corehub ready', port: PORT, admin: ADMIN_PORT, tls: TLS })}\n`);
});

process.on('SIGTERM', () => process.exit(0));
