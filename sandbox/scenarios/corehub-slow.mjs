import { jobSpec } from '../lib/chronos.mjs';
import { corehub } from '../lib/stack.mjs';
import { request, sleep } from '../lib/util.mjs';
import { pipelineState, ranOk } from './common.mjs';

const DELAY_MS = 135000;

export default {
  name: 'corehub-slow',
  description: 'Core Hub answers a pipeline command after 135 s (the old 120 s internal timeout): the job waits for the answer and its outcome matches what Core Hub did',
  async run(ctx) {
    const { api } = await ctx.resetMain();
    ctx.usedMain = true;
    const job = (await api.createJob(jobSpec('pipeline_stop', { name: 'slow stop', pipeline_id: 'pl-orders' }))).json;
    await corehub.addFault({ mode: 'slow', delayMs: DELAY_MS, count: 1, match: { path: '/pipelines/pl-orders/commands/sync/stop' } });
    const since = await ctx.mark();
    const began = Date.now();
    let finished = null;
    const run = api.runJob(job.id).then(r => {
      finished = Date.now() - began;
      return r;
    });
    const probes = [];
    let activeAtTimeout = null;
    while (finished === null) {
      await sleep(10000);
      probes.push((await request(api.url('/'), { timeoutMs: 3000 })).status);
      if (finished !== null) activeAtTimeout = (await pipelineState('pl-orders')).entities.filter(e => e.active).length;
    }
    const r = await run;
    activeAtTimeout = (await pipelineState('pl-orders')).entities.filter(e => e.active).length;
    const after = (await api.getJob(job.id)).json;
    ctx.check('the run waits for Core Hub\'s answer instead of giving up after 120 s', ranOk(r) && finished >= DELAY_MS - 5000, `HTTP ${r.status} after ${Math.round(finished / 1000)} s: ${r.json?.message || r.text?.slice(0, 160)}`);
    ctx.check('the job is recorded as successful', !!after.last_successful_run && !after.last_error_message, `last_successful_run=${after.last_successful_run} last_error_message=${after.last_error_message}`);
    ctx.check('Chronos stays responsive while the command is pending', probes.length > 0 && probes.every(s => s === 307), `probes ${probes.join(',')}`);

    await sleep(Math.max(0, DELAY_MS - (Date.now() - began)) + 8000);
    const hubCall = (await ctx.calls(since, { tag: 'chronos', only: /sync\/stop/ }))[0];
    const stateAfter = await pipelineState('pl-orders');
    const applied = stateAfter.entities.every(e => !e.active);
    const evidence = {
      jobReportedFailedAfterSeconds: Math.round(finished / 1000),
      coreHubAnsweredAfterMs: hubCall?.ms,
      coreHubStatus: hubCall?.status,
      activeEntitiesWhenJobFailed: activeAtTimeout,
      allEntitiesStoppedLater: applied,
      jobLastError: after.last_error_message,
    };
    ctx.check('the job outcome matches what Core Hub did (no command reported failed and applied anyway)', ranOk(r) === applied, `job ${ranOk(r) ? 'succeeded' : 'failed'}; Core Hub answered ${hubCall?.status} after ${hubCall?.ms} ms and the pipeline ended up ${applied ? 'stopped' : 'unchanged'}`, {
      finding: {
        id: 'slow-corehub-false-failure',
        title: 'A command Core Hub answers after 120 s is applied although the job was already reported failed',
        severity: 'observation',
        detail: 'The scheduled job must not give up on Chronos\' own route (SCHEDULER_INTERNAL_HTTP_TIMEOUT) before the Core Hub request it waits on (CHRONOS_COREHUB_TIMEOUT) can still succeed; otherwise the job is recorded as failed while Core Hub executes the command.',
        evidence,
      },
      evidence,
    });
    ctx.save('summary.json', { ...evidence, probes });
  },
};
