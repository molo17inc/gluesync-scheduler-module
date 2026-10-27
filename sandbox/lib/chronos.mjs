import { env } from './env.mjs';
import { corehub, userToken } from './stack.mjs';
import { nowIso, request, sleep } from './util.mjs';

// A client for one Chronos instance's public REST API. Authenticates the way the UI
// does: the Core Hub session token goes in the `gs-auth` cookie, and Chronos asks
// Core Hub (/authentication/me) who it belongs to. Every call is added to the
// scenario's API transcript.
export class ChronosApi {
  constructor(inst, transcript = []) {
    this.inst = inst;
    this.transcript = transcript;
  }

  url(p) {
    return `${env.scheme}://127.0.0.1:${this.inst.hostPort}${p}`;
  }

  async call(label, method, p, { user = 'admin', body, query, headers = {}, timeoutMs = 30000, anonymous = false } = {}) {
    const qs = query ? `?${new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)]))}` : '';
    const hdrs = { ...headers };
    if (!anonymous) hdrs.cookie = `gs-auth=${await userToken(this.inst, user)}`;
    const started = nowIso();
    const res = await request(this.url(`${p}${qs}`), { method, body, headers: hdrs, timeoutMs });
    this.transcript.push({
      ts: started,
      instance: this.inst.label,
      label,
      user: anonymous ? null : user,
      method,
      path: `${p}${qs}`,
      body,
      status: res.status,
      ms: res.ms,
      error: res.error,
      json: res.json,
      text: res.json === undefined ? res.text?.slice(0, 800) : undefined,
    });
    return res;
  }

  get(label, p, o) {
    return this.call(label, 'GET', p, o);
  }

  post(label, p, body, o = {}) {
    return this.call(label, 'POST', p, { ...o, body });
  }

  put(label, p, body, o = {}) {
    return this.call(label, 'PUT', p, { ...o, body });
  }

  patch(label, p, body, o = {}) {
    return this.call(label, 'PATCH', p, { ...o, body });
  }

  del(label, p, o) {
    return this.call(label, 'DELETE', p, o);
  }

  // ---- jobs

  async createJob(spec, o = {}) {
    const res = await this.post(`create job ${spec.name}`, '/api/jobs/', spec, o);
    return res;
  }

  getJob(id, o) {
    return this.get(`get job ${id}`, `/api/jobs/${id}`, o);
  }

  listJobs(query, o = {}) {
    return this.get('list jobs', '/api/jobs/', { ...o, query: { limit: 500, ...(query || {}) } });
  }

  runJob(id, o) {
    return this.post(`run job ${id}`, `/api/jobs/${id}/run`, undefined, { timeoutMs: 400000, ...(o || {}) });
  }

  setEnabled(id, enabled, o) {
    return this.patch(`${enabled ? 'enable' : 'disable'} job ${id}`, `/api/jobs/${id}/status`, { enabled }, o);
  }

  deleteJob(id, o) {
    return this.del(`delete job ${id}`, `/api/jobs/${id}`, o);
  }

  async waitJob(id, pred, { timeoutMs = 60000, intervalMs = 1500 } = {}) {
    const began = Date.now();
    let last;
    while (Date.now() - began < timeoutMs) {
      const r = await this.getJob(id);
      last = r.json;
      if (r.status === 200 && pred(r.json)) return r.json;
      await sleep(intervalMs);
    }
    return null;
  }
}

// Default job spec. The cron fires once a year so nothing runs unless asked to.
export const jobSpec = (taskType, over = {}) => ({
  name: `sandbox ${taskType}`,
  task_type: taskType,
  cron_expression: '0 0 1 1 *',
  pipeline_id: 'pl-orders',
  ...over,
});

export const adminOf = () => corehub;
