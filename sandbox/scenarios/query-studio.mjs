import { jobSpec } from '../lib/chronos.mjs';
import { bodyOf, ranFailed, ranOk } from './common.mjs';

const exec = /query-studio\/pipelines\/.+\/execute$/;

export default {
  name: 'query-studio',
  description: 'Query Studio: agent list passthrough, read-only guard, writable queries, saved query live SQL and fallback to the stored snapshot',
  async run(ctx) {
    const { api } = await ctx.resetMain();
    ctx.usedMain = true;
    const mk = async over => (await api.createJob(jobSpec('query_studio', { agent_id: 'agent-pg', ...over }))).json;
    const run = async (job, user = 'admin') => {
      const mark = await ctx.mark();
      const r = await api.runJob(job.id, { user });
      const calls = await ctx.calls(mark, { tag: 'chronos' });
      const after = (await api.getJob(job.id)).json;
      return { r, calls, after, post: calls.find(c => exec.test(c.path)) };
    };

    const agents = await api.get('query studio agents', '/api/query-studio/agents');
    ctx.check('agent list is the Core Hub payload', agents.status === 200 && agents.json?.length === 2 && agents.json[0].id === 'agent-pg', `HTTP ${agents.status} ${JSON.stringify(agents.json)?.slice(0, 120)}`);
    ctx.check('agent list needs authentication', (await api.get('agents anonymous', '/api/query-studio/agents', { anonymous: true })).status === 401, '');

    const ok = await mk({ name: 'qs select', query_sql: 'SELECT count(*) FROM orders' });
    const a = await run(ok);
    ctx.check('a SELECT runs read-only by default', ranOk(a.r) && bodyOf(a.post)?.options?.readOnly === true, `HTTP ${a.r.status} body ${a.post?.req}`);

    const write = await mk({ name: 'qs write blocked', query_sql: "UPDATE orders SET status = 'x'" });
    const b = await run(write);
    ctx.check('a write statement with query_read_only=true is refused by Core Hub and fails the job', ranFailed(b.r) && b.post?.status === 403 && !!b.after.last_error_message, `HTTP ${b.r.status}, Core Hub ${b.post?.status}, error=${b.after.last_error_message}`);

    const writable = await mk({ name: 'qs write allowed', query_sql: "UPDATE orders SET status = 'x'", query_read_only: false });
    const c = await run(writable);
    ctx.check('query_read_only=false sends options.readOnly=false and the write goes through', ranOk(c.r) && bodyOf(c.post)?.options?.readOnly === false, `HTTP ${c.r.status} body ${c.post?.req}`);

    const failing = await mk({ name: 'qs failing query', query_sql: 'SELECT SANDBOX_FAIL' });
    const d = await run(failing);
    ctx.check('a query Core Hub reports as ERROR (HTTP 200) fails the job', ranFailed(d.r) && !!d.after.last_error_message, `HTTP ${d.r.status}, error=${d.after.last_error_message}`);

    const saved = await mk({ name: 'qs saved live', saved_query_id: 'sq-orders', query_sql: 'SELECT 1 /* old snapshot */' });
    const e = await run(saved);
    ctx.check('a saved query uses the live SQL from Core Hub', ranOk(e.r) && bodyOf(e.post)?.sql === 'SELECT count(*) FROM orders' && e.calls.some(x => x.path === '/query-studio/saved-queries/sq-orders'), `body ${e.post?.req}`);

    const gone = await mk({ name: 'qs saved missing', saved_query_id: 'sq-missing', query_sql: 'SELECT 42 /* snapshot */' });
    const f = await run(gone);
    ctx.check('a saved query that no longer exists falls back to the stored SQL', ranOk(f.r) && bodyOf(f.post)?.sql === 'SELECT 42 /* snapshot */' && f.calls.some(x => x.path === '/query-studio/saved-queries/sq-missing' && x.status === 404), `HTTP ${f.r.status} body ${f.post?.req}`);

    const noAgent = await api.createJob(jobSpec('query_studio', { name: 'qs no agent', query_sql: 'SELECT 1' }));
    ctx.check('query_studio without an agent is rejected (4xx)', noAgent.status >= 400 && noAgent.status < 500, `HTTP ${noAgent.status}`);
    const noSql = await api.createJob(jobSpec('query_studio', { name: 'qs no sql', agent_id: 'agent-pg' }));
    ctx.check('query_studio without SQL or saved query is rejected (4xx)', noSql.status >= 400 && noSql.status < 500, `HTTP ${noSql.status}`);
  },
};
