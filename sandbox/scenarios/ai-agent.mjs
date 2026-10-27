import { jobSpec } from '../lib/chronos.mjs';
import { bodyOf, ranFailed, ranOk } from './common.mjs';

const runsPost = /agents\/.+\/runs$/;

export default {
  name: 'ai-agent',
  description: 'AI agent runs: published agent POST with input template, polling to a terminal status, failures, approval wait, validation',
  async run(ctx) {
    const { api } = await ctx.resetMain();
    ctx.usedMain = true;
    const spec = over => jobSpec('ai_agent_run', { pipeline_id: '', prompt_template: 'Summarise {{pipeline.name}}', payload_allow_list: ['pipeline.name'], agent_input: { tone: 'brief' }, ...over });
    const run = async over => {
      const created = await api.createJob(spec(over));
      if (created.status !== 201) return { created };
      const mark = await ctx.mark();
      const r = await api.runJob(created.json.id);
      const calls = await ctx.calls(mark, { tag: 'chronos' });
      return { created, r, calls, post: calls.find(c => runsPost.test(c.path)), polls: calls.filter(c => c.method === 'GET' && /ai\/v1\/runs\//.test(c.path)), after: (await api.getJob(created.json.id)).json };
    };

    const ok = await run({ name: 'ai ok', agent_alias: 'daily-digest', agent_version: 3, idempotency_key: 'sandbox-key' });
    ctx.check('a published agent run succeeds after polling the run', ranOk(ok.r) && ok.polls.length >= 2, `HTTP ${ok.r?.status}, ${ok.polls?.length} poll(s)`);
    const body = bodyOf(ok.post) || {};
    ctx.check('the POST carries the input, agent version and idempotency key', body.agentVersion === 3 && body.idempotencyKey === 'sandbox-key' && !!body.input && body.input.tone === 'brief', ok.post?.req);
    ctx.note(`AI run request body sent to Core Hub: ${ok.post?.req}`);
    ctx.check('the run is a POST to /api/ai/v1/agents/{alias}/runs', ok.post?.path === '/api/ai/v1/agents/daily-digest/runs', ok.post?.path);

    const unknown = await run({ name: 'ai unknown agent', agent_alias: 'unknown-agent' });
    ctx.check('an unknown agent fails the job with the Core Hub error recorded', ranFailed(unknown.r) && !!unknown.after?.last_error_message, `HTTP ${unknown.r?.status} err=${unknown.after?.last_error_message}`);
    const failing = await run({ name: 'ai failing run', agent_alias: 'failing-agent' });
    ctx.check('a run that ends FAILED fails the job', ranFailed(failing.r) && !!failing.after?.last_error_message, `HTTP ${failing.r?.status} err=${failing.after?.last_error_message}`);
    const approval = await run({ name: 'ai approval', agent_alias: 'approval-agent' });
    ctx.check('a run that waits for approval keeps being polled until it finishes', ranOk(approval.r) && approval.polls.length >= 3, `HTTP ${approval.r?.status}, ${approval.polls?.length} poll(s)`);

    const badToken = await api.createJob(spec({ name: 'ai token not allowed', agent_alias: 'daily-digest', prompt_template: 'Hello {{secret.value}}' }));
    ctx.check('a template token outside payload_allow_list is rejected (4xx)', badToken.status >= 400 && badToken.status < 500, `HTTP ${badToken.status}`);
    const unsafe = await api.createJob(spec({ name: 'ai unsafe path', agent_alias: 'daily-digest', payload_allow_list: ['a.__class__'] }));
    ctx.check('an unsafe payload path is rejected (4xx)', unsafe.status >= 400 && unsafe.status < 500, `HTTP ${unsafe.status}`);
    const noAlias = await api.createJob(spec({ name: 'ai no alias', agent_alias: '' }));
    ctx.check('an empty agent alias is rejected (4xx)', noAlias.status >= 400 && noAlias.status < 500, `HTTP ${noAlias.status}`);
  },
};
