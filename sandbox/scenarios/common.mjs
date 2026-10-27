import { corehub } from '../lib/stack.mjs';
import { sleep } from '../lib/util.mjs';

export const ents = (pipeline, ...n) => n.map(i => `${pipeline}-e${i}`);

export const setEntity = (pipeline, entity, patch) => corehub.post(`/__sandbox/entities/${pipeline}/${entity}`, patch);

export const pipelineState = async id => (await corehub.state()).pipelines.find(p => p.id === id);

// Polls the fake's call log until `pred(items)` holds.
export const waitCalls = async (ctx, since, tag, pred, { timeoutMs = 60000, intervalMs = 1500, only } = {}) => {
  const began = Date.now();
  let items = [];
  while (Date.now() - began < timeoutMs) {
    items = await ctx.calls(since, { tag, only });
    if (pred(items)) return { ok: true, items };
    await sleep(intervalMs);
  }
  return { ok: false, items };
};

export const bodyOf = entry => {
  try {
    return entry.req ? JSON.parse(entry.req) : undefined;
  } catch {
    return undefined;
  }
};

// A run-now answers HTTP 200 with success=false when the job's work failed.
export const ranOk = r => r?.status === 200 && r.json?.success !== false;
export const ranFailed = r => !r || r.status >= 400 || r.json?.success === false;
