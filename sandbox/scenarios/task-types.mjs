import { jobSpec } from '../lib/chronos.mjs';
import { corehub } from '../lib/stack.mjs';
import { bodyOf, ents, pipelineState, setEntity } from './common.mjs';

const P = 'pl-orders';
const [E1, E2] = ents(P, 1, 2);
const cmd = (action, ...kv) => `POST /pipelines/${P}/commands/sync/${action} ${kv.join(' ')}`.trim();
const UPSERT = 'snapshotWriteMethod=UPSERT';

const allInactive = async () => {
  for (const e of ents(P, 1, 2, 3, 4)) await setEntity(P, e, { active: false });
};

const activeOf = async () => (await pipelineState(P)).entities.filter(e => e.active).map(e => e.id);

// One row per TaskType: what the job asks for, what Chronos must send to Core Hub
// (exact and ordered, authentication calls aside) and how the model must end up.
const table = [
  {
    type: 'entity_start',
    spec: { entity_ids: [E1, E2] },
    pre: async () => allInactive(),
    expect: [cmd('start', `entity=${E1}`, 'withSnapshot=false', UPSERT), cmd('start', `entity=${E2}`, 'withSnapshot=false', UPSERT)],
    model: async () => [(await activeOf()).sort().join(), [E1, E2].sort().join()],
  },
  {
    type: 'entity_start',
    label: 'entity_start with snapshot',
    spec: { entity_ids: [E1], with_snapshot: true },
    pre: async () => allInactive(),
    expect: [cmd('start', `entity=${E1}`, 'withSnapshot=true', UPSERT)],
  },
  {
    type: 'entity_stop',
    spec: { entity_ids: [E1, E2] },
    expect: [cmd('stop', `entity=${E1}`), cmd('stop', `entity=${E2}`)],
    model: async () => [(await activeOf()).sort().join(), ents(P, 3, 4).sort().join()],
  },
  {
    type: 'pipeline_start',
    spec: {},
    pre: async () => allInactive(),
    expect: [cmd('start', 'withSnapshot=false', UPSERT)],
    model: async () => [(await activeOf()).length, 4],
  },
  {
    type: 'pipeline_stop',
    spec: {},
    expect: [cmd('stop')],
    model: async () => [(await activeOf()).length, 0],
  },
  {
    type: 'entity_snapshot',
    spec: { entity_ids: [E1] },
    expect: [cmd('stop', `entity=${E1}`), cmd('one-time-snapshot', `entity=${E1}`, UPSERT, 'fromScratch=true')],
  },
  {
    type: 'pipeline_snapshot',
    spec: { snapshot_write_method: 'INSERT' },
    expect: [cmd('stop'), cmd('one-time-snapshot', 'snapshotWriteMethod=INSERT', 'fromScratch=true')],
  },
  {
    type: 'group_start',
    spec: { group_ids: ['grp-a'] },
    pre: async () => allInactive(),
    expect: [cmd('start-group', 'groupId=grp-a', 'withSnapshot=false', UPSERT)],
    model: async () => [(await activeOf()).sort().join(), [E1, E2].sort().join()],
  },
  {
    type: 'group_stop',
    spec: { group_ids: ['grp-b'] },
    expect: [cmd('stop-group', 'groupId=grp-b')],
    model: async () => [(await activeOf()).sort().join(), [E1, E2].sort().join()],
  },
  {
    type: 'group_snapshot',
    spec: { group_ids: ['grp-a'] },
    expect: [cmd('stop-group', 'groupId=grp-a'), cmd('one-time-snapshot-group', 'groupId=grp-a', UPSERT, 'fromScratch=true')],
  },
  {
    type: 'entity_redo',
    spec: { entity_ids: [E1] },
    expect: [cmd('stop', `entity=${E1}`), `GET /pipelines/${P}/entities-status`, cmd('redo', `entity=${E1}`, 'withSnapshot=true', UPSERT)],
  },
  {
    type: 'pipeline_redo',
    spec: {},
    expect: [cmd('stop'), `GET /pipelines/${P}/entities`, `GET /pipelines/${P}/entities-status`, cmd('redo', 'withSnapshot=true', UPSERT)],
  },
  {
    type: 'group_redo',
    spec: { group_ids: ['grp-a'] },
    expect: [cmd('stop-group', 'groupId=grp-a'), `GET /pipelines/${P}/config/entities`, `GET /pipelines/${P}/entities-status`, cmd('redo-group', 'groupId=grp-a', 'withSnapshot=false', UPSERT)],
  },
  {
    type: 'pipeline_enter_maintenance',
    spec: { pipeline_id: 'pl-maint-a' },
    expect: ['POST /pipelines/pl-maint-a/commands/maintenance/enter'],
    model: async () => [(await pipelineState('pl-maint-a')).maintenance, true],
  },
  {
    type: 'pipeline_exit_maintenance',
    spec: { pipeline_id: 'pl-maint-a' },
    pre: async () => corehub.setMaintenance('pl-maint-a', true),
    expect: ['POST /pipelines/pl-maint-a/commands/maintenance/exit'],
    model: async () => [(await pipelineState('pl-maint-a')).maintenance, false],
  },
  {
    type: 'query_studio',
    label: 'query_studio custom SQL',
    spec: { agent_id: 'agent-pg', query_sql: 'SELECT count(*) FROM orders' },
    expect: [`POST /query-studio/pipelines/${P}/agents/agent-pg/execute`],
    body: b => b?.sql === 'SELECT count(*) FROM orders' && b?.options?.readOnly === true,
  },
  {
    type: 'query_studio',
    label: 'query_studio saved query (live SQL)',
    spec: { agent_id: 'agent-pg', saved_query_id: 'sq-orders', query_sql: 'SELECT 1 /* stale snapshot */' },
    expect: ['GET /query-studio/saved-queries/sq-orders', `POST /query-studio/pipelines/${P}/agents/agent-pg/execute`],
    body: b => b?.sql === 'SELECT count(*) FROM orders',
  },
  {
    type: 'ai_agent_run',
    spec: { pipeline_id: '', agent_alias: 'daily-digest', prompt_template: 'Summarise {{pipeline.name}}', payload_allow_list: ['pipeline.name'], agent_input: { tone: 'brief' } },
    expect: ['POST /api/ai/v1/agents/daily-digest/runs', 'GET /api/ai/v1/runs/run-*'],
    loose: true,
  },
  {
    type: 'entity_validate',
    spec: { entity_ids: [E1] },
    expect: ['POST /data-comparison/runs', 'GET /data-comparison/runs/val-*'],
    loose: true,
  },
  {
    type: 'brain_reindex',
    spec: { pipeline_id: '' },
    expect: ['GET /api/ai/v1/brain/job', 'POST /api/ai/v1/brain/job', 'GET /api/ai/v1/brain/job'],
    loose: true,
  },
  {
    type: 'visualize_refresh',
    spec: { pipeline_id: '', viz_id: 'viz-sales', visualize_parameters: { region: 'EMEA' } },
    expect: ['POST /visualize/refresh'],
    body: b => b?.task === 'visualize.refresh' && b?.vizId === 'viz-sales' && b?.parameters?.region === 'EMEA' && Number.isInteger(b?.scheduleId),
  },
];

// Wildcard-aware ordered match for polled endpoints ("run-*"), exact for the rest.
const reduce = (items, loose) => {
  if (!loose) return items;
  const out = [];
  items.forEach(e => {
    const last = out[out.length - 1];
    const norm = e.path.replace(/\/(run|val)-\d+$/, '/$1-*');
    if (last && last.method === e.method && last.norm === norm && e.method === 'GET') return;
    out.push({ ...e, norm, path: norm });
  });
  return out;
};

export default {
  name: 'task-types',
  description: 'Every TaskType: create a job, run it now and assert the exact Core Hub calls and model effects',
  async run(ctx) {
    const { api } = await ctx.resetMain();
    ctx.usedMain = true;

    for (const row of table) {
      const name = row.label || row.type;
      await corehub.post('/__sandbox/reset');
      if (row.pre) await row.pre();
      const since = await ctx.mark();
      const created = await api.createJob(jobSpec(row.type, { name: `task-types ${name}`, ...row.spec }));
      if (!ctx.check(`${name}: job created`, created.status === 201, `HTTP ${created.status} ${created.status === 201 ? '' : JSON.stringify(created.json)}`)) continue;
      const id = created.json.id;
      const ran = await api.runJob(id);
      ctx.check(`${name}: run-now succeeds`, ran.status === 200 && ran.json?.success === true, `HTTP ${ran.status} ${ran.json?.message || ran.text}`);
      const calls = reduce(await ctx.calls(since, { tag: 'chronos' }), row.loose);
      ctx.expectCalls(`${name}: Core Hub calls`, calls, row.expect);
      if (row.body) {
        const post = calls.find(c => c.method === 'POST');
        ctx.check(`${name}: request body`, !!post && row.body(bodyOf(post)), `body ${post?.req}`);
      }
      if (row.model) {
        const [actual, wanted] = await row.model();
        ctx.check(`${name}: Core Hub model after`, JSON.stringify(actual) === JSON.stringify(wanted), `actual ${JSON.stringify(actual)} wanted ${JSON.stringify(wanted)}`);
      }
      const after = await api.getJob(id);
      ctx.check(`${name}: job bookkeeping`, !!after.json?.last_run && !!after.json?.last_successful_run && !after.json?.last_error_message, `last_run=${after.json?.last_run} ok=${after.json?.last_successful_run} err=${after.json?.last_error_message}`);
      await api.deleteJob(id);
    }
  },
};
