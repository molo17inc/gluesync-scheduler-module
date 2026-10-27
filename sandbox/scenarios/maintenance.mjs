import { jobSpec } from '../lib/chronos.mjs';
import { containerLogs, corehub } from '../lib/stack.mjs';
import { pipelineState, ranFailed, ranOk } from './common.mjs';

const MSG = pid => `Pipeline with id ${pid} is in maintenance. Please wait until the maintenance mode is disabled.`;

export default {
  name: 'maintenance',
  description: 'Maintenance mode: enter/exit jobs, and what happens to sync jobs that Core Hub refuses with 406 while a pipeline is in maintenance',
  async run(ctx) {
    const { inst, api } = await ctx.resetMain();
    ctx.usedMain = true;
    const since = new Date().toISOString();
    const P = 'pl-maint-a';
    const mk = async (type, over = {}) => (await api.createJob(jobSpec(type, { name: `maintenance ${type}`, pipeline_id: P, ...over }))).json;

    const enter = await mk('pipeline_enter_maintenance');
    const exit = await mk('pipeline_exit_maintenance');
    const start = await mk('pipeline_start');
    const stopEntity = await mk('entity_stop', { entity_ids: [`${P}-e1`] });
    const groupStart = await mk('group_start', { group_ids: ['grp-a'] });
    const snapshot = await mk('pipeline_snapshot');

    const e1 = await api.runJob(enter.id);
    ctx.check('enter-maintenance job succeeds', e1.status === 200, `HTTP ${e1.status}`);
    ctx.check('Core Hub shows the pipeline in maintenance', (await pipelineState(P)).maintenance === true, '');
    const e2 = await api.runJob(enter.id);
    ctx.check('entering maintenance twice is harmless', e2.status === 200, `HTTP ${e2.status}`);

    const mark = await ctx.mark();
    const runs = {};
    for (const [name, job] of [['pipeline_start', start], ['entity_stop', stopEntity], ['group_start', groupStart], ['pipeline_snapshot', snapshot]]) {
      const r = await api.runJob(job.id);
      const after = (await api.getJob(job.id)).json;
      runs[name] = { status: r.status, message: r.json?.detail || r.json?.message || r.text, lastError: after.last_error_message, lastRunErrorTime: after.last_run_error_time, lastSuccess: after.last_successful_run };
      ctx.check(`${name} on a pipeline in maintenance is reported as a failure`, r.status >= 400 || r.json?.success === false, `HTTP ${r.status}: ${runs[name].message}`);
      ctx.check(`${name}: the failure is recorded on the job`, !!after.last_error_message && !!after.last_run_error_time && !after.last_successful_run, `last_error_message=${after.last_error_message}`);
    }
    ctx.save('maintenance-runs.json', runs);
    const calls = await ctx.calls(mark, { tag: 'chronos' });
    const refused = calls.filter(c => c.status === 406);
    ctx.check('Core Hub answered 406 to the sync commands', refused.length >= 4 && refused.every(c => String(c.res).includes(MSG(P))), `${refused.length} x 406: ${refused.map(c => c.path).join(', ')}`);

    const logs = await containerLogs(inst.name, { since });
    ctx.check('Chronos logs Core Hub\'s maintenance message', logs.includes(MSG(P)), 'searched the instance log');
    const surfaced = Object.values(runs).every(r => /maintenance/i.test(`${r.message} ${r.lastError}`));
    ctx.check('the job error shown to the user says the pipeline is in maintenance', surfaced, JSON.stringify(Object.fromEntries(Object.entries(runs).map(([k, v]) => [k, v.lastError]))), {
      finding: {
        id: 'maintenance-406-reason-lost',
        title: 'The reason for a refused command (pipeline in maintenance, HTTP 406) is only in the log, not in the job error',
        severity: 'observation',
        detail: 'Core Hub answers 406 "Pipeline with id X is in maintenance. Please wait until the maintenance mode is disabled." The job records a generic "Failed to ..." message, so the UI cannot tell the user why a scheduled start/stop/snapshot did not happen.',
        evidence: runs,
      },
    });

    const x = await api.runJob(exit.id);
    ctx.check('exit-maintenance job succeeds', x.status === 200, `HTTP ${x.status}`);
    ctx.check('Core Hub shows the pipeline out of maintenance', (await pipelineState(P)).maintenance === false, '');
    const again = await api.runJob(start.id);
    ctx.check('sync job works again after maintenance', again.status === 200, `HTTP ${again.status}`);

    // A refusal injected by the fake without the pipeline being in maintenance.
    await corehub.addFault({ mode: 'maintenance406', pipeline: 'pl-orders', match: { path: '/pipelines/pl-orders/commands/sync' }, count: 1 });
    const forced = await mk('pipeline_stop', { pipeline_id: 'pl-orders', name: 'forced 406' });
    const f1 = await api.runJob(forced.id);
    const f2 = await api.runJob(forced.id);
    ctx.check('injected 406 fails the first run and the next run recovers', ranFailed(f1) && ranOk(f2), `HTTP ${f1.status}/${f1.json?.success} then ${f2.status}/${f2.json?.success}`);
  },
};
