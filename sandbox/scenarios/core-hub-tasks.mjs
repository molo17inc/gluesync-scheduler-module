import { jobSpec } from '../lib/chronos.mjs';
import { corehub } from '../lib/stack.mjs';
import { bodyOf, ents, ranFailed, ranOk } from './common.mjs';

export default {
  name: 'core-hub-tasks',
  description: 'Entity validation (Validator), Enterprise brain reindex and Visualize refresh: success, differences, reconcile, failure and retry paths',
  async run(ctx) {
    const { api } = await ctx.resetMain();
    ctx.usedMain = true;
    const P = 'pl-orders';
    const [E1] = ents(P, 1);
    const run = async (type, over) => {
      const created = await api.createJob(jobSpec(type, over));
      if (created.status !== 201) return { created };
      const mark = await ctx.mark();
      const began = Date.now();
      const r = await api.runJob(created.json.id);
      const calls = await ctx.calls(mark, { tag: 'chronos' });
      return { created, r, calls, ms: Date.now() - began, after: (await api.getJob(created.json.id)).json };
    };

    // ---- entity validation
    const clean = await run('entity_validate', { name: 'validate clean', entity_ids: [E1] });
    ctx.check('validation without differences succeeds', ranOk(clean.r) && clean.calls.some(c => c.method === 'POST' && c.path === '/data-comparison/runs'), `HTTP ${clean.r?.status}`);
    const diff = await run('entity_validate', { name: 'validate diff', entity_ids: [`${P}-diff`] });
    ctx.check('differences without reconcile fail the job and the breakdown reaches last_error_message', ranFailed(diff.r) && /3 only in source/.test(diff.after?.last_error_message || ''), `HTTP ${diff.r?.status} err=${diff.after?.last_error_message}`);
    const rec = await run('entity_validate', { name: 'validate reconcile', entity_ids: [`${P}-diff`], validation_reconcile: true });
    ctx.check('differences with reconcile are written back (POST .../reconciliation) and the job succeeds', ranOk(rec.r) && rec.calls.some(c => c.method === 'POST' && /reconciliation$/.test(c.path)) && bodyOf(rec.calls.find(c => /reconciliation$/.test(c.path)))?.reconcileAll === true, `HTTP ${rec.r?.status} calls ${rec.calls.map(c => c.method + ' ' + c.path).join(', ')}`);
    const broken = await run('entity_validate', { name: 'validate broken', entity_ids: [`${P}-broken`] });
    ctx.check('a failed comparison fails the job with the Core Hub reason', ranFailed(broken.r) && /comparison engine crashed/.test(broken.after?.last_error_message || ''), `err=${broken.after?.last_error_message}`);
    const none = await api.createJob(jobSpec('entity_validate', { name: 'validate nothing' }));
    ctx.check('entity_validate without entities is rejected (4xx)', none.status >= 400 && none.status < 500, `HTTP ${none.status}`);

    // ---- brain reindex
    const brain = await run('brain_reindex', { name: 'brain ok', pipeline_id: '' });
    const brainPosts = brain.calls.filter(c => c.method === 'POST' && c.path === '/api/ai/v1/brain/job');
    ctx.check('brain reindex starts a rebuild and waits for READY', ranOk(brain.r) && brainPosts.length === 1 && brain.ms >= 2500, `HTTP ${brain.r?.status}, ${brainPosts.length} POST, took ${brain.ms} ms`);
    await corehub.post('/__sandbox/brain', { fail: true });
    const brainFail = await run('brain_reindex', { name: 'brain fails', pipeline_id: '' });
    ctx.check('a failed rebuild fails the job with the brain message', ranFailed(brainFail.r) && /Brain rebuild failed/.test(brainFail.after?.last_error_message || ''), `HTTP ${brainFail.r?.status} err=${brainFail.after?.last_error_message}`);
    await corehub.post('/__sandbox/brain', { fail: false });

    // ---- visualize refresh
    const viz = await run('visualize_refresh', { name: 'viz ok', pipeline_id: '', viz_id: 'viz-sales', visualize_parameters: { region: 'EMEA', year: '2026' } });
    const vizPost = viz.calls.find(c => c.path === '/visualize/refresh');
    ctx.check('refresh is POST /visualize/refresh with task, vizId, parameters and scheduleId', ranOk(viz.r) && bodyOf(vizPost)?.task === 'visualize.refresh' && bodyOf(vizPost)?.parameters?.year === '2026' && bodyOf(vizPost)?.scheduleId === viz.created.json.id, `body ${vizPost?.req}`);
    const gone = await run('visualize_refresh', { name: 'viz gone', pipeline_id: '', viz_id: 'viz-gone' });
    ctx.check('a visualization that no longer exists (404) fails the job and disables the schedule', ranFailed(gone.r) && gone.after?.enabled === false, `HTTP ${gone.r?.status} enabled=${gone.after?.enabled}`);
    const bad = await run('visualize_refresh', { name: 'viz bad', pipeline_id: '', viz_id: 'viz-bad' });
    ctx.check('a 4xx from Core Hub is terminal: one POST, error message kept, schedule stays enabled', ranFailed(bad.r) && bad.calls.filter(c => c.path === '/visualize/refresh').length === 1 && /Invalid parameters/.test(bad.after?.last_error_message || '') && bad.after?.enabled === true, `posts ${bad.calls.filter(c => c.path === '/visualize/refresh').length} err=${bad.after?.last_error_message}`);
    const flaky = await run('visualize_refresh', { name: 'viz flaky', pipeline_id: '', viz_id: 'viz-flaky' });
    const flakyPosts = flaky.calls.filter(c => c.path === '/visualize/refresh');
    ctx.check('5xx answers are retried with backoff until Core Hub accepts', ranOk(flaky.r) && flakyPosts.map(c => c.status).join() === '503,503,202', `HTTP ${flaky.r?.status} statuses ${flakyPosts.map(c => c.status).join()}`);
    const noViz = await api.createJob(jobSpec('visualize_refresh', { name: 'viz none', pipeline_id: '' }));
    ctx.check('visualize_refresh without viz_id is rejected (4xx)', noViz.status >= 400 && noViz.status < 500, `HTTP ${noViz.status}`);
  },
};
