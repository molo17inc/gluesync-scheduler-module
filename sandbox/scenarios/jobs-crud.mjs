import { jobSpec } from '../lib/chronos.mjs';
import { ents } from './common.mjs';

const P = 'pl-orders';

export default {
  name: 'jobs-crud',
  description: 'Scheduled jobs API: create (cron and schedule), validation, list filters, get, update, enable/disable, delete, role permissions',
  async run(ctx) {
    const { api } = await ctx.resetMain();
    ctx.usedMain = true;
    const [E1, E2] = ents(P, 1, 2);

    // ---- create
    const c1 = await api.createJob(jobSpec('entity_start', { name: 'crud entity start', cron_expression: '*/15 * * * *', entity_ids: [E1, E2], with_snapshot: true, snapshot_write_method: 'INSERT' }));
    ctx.check('create with cron expression returns 201', c1.status === 201, `HTTP ${c1.status} ${c1.status === 201 ? '' : JSON.stringify(c1.json)}`);
    const j1 = c1.json || {};
    ctx.check('created job carries id, identifier, command and timezone', !!j1.id && /^gluesync_job_/.test(j1.cron_job_identifier || '') && /Scheduled job ID: job_/.test(j1.command || '') && j1.timezone_name === 'Europe/Rome', `${j1.id} ${j1.cron_job_identifier} ${j1.command} ${j1.timezone_name}`);
    ctx.check('created job keeps entity_ids, snapshot flags and is_cron_expression', JSON.stringify(j1.entity_ids) === JSON.stringify([E1, E2]) && j1.with_snapshot === true && j1.snapshot_write_method === 'INSERT' && j1.is_cron_expression === true, JSON.stringify({ e: j1.entity_ids, s: j1.with_snapshot, m: j1.snapshot_write_method, c: j1.is_cron_expression }));
    ctx.check('created job has a next_run', !!j1.next_run, String(j1.next_run));

    const c2 = await api.createJob(jobSpec('pipeline_stop', { name: 'crud schedule form', cron_expression: undefined, schedule: { days_of_week: ['monday', 'wednesday'], hour: 8, minute: 30 } }));
    ctx.check('create with schedule form returns 201 and a cron expression', c2.status === 201 && c2.json?.cron_expression === '30 8 * * 1,3' && c2.json?.is_cron_expression === false, `HTTP ${c2.status} cron=${c2.json?.cron_expression}`);
    ctx.check('schedule form exposes schedule_days', JSON.stringify(c2.json?.schedule_days) === JSON.stringify(['monday', 'wednesday']), JSON.stringify(c2.json?.schedule_days));

    const c3 = await api.createJob(jobSpec('pipeline_start', { name: 'crud disabled', enabled: false, cron_expression: '0 3 * * *' }));
    ctx.check('create disabled job returns 201 with enabled=false', c3.status === 201 && c3.json?.enabled === false, `HTTP ${c3.status}`);

    // ---- validation
    const bad1 = await api.createJob(jobSpec('group_start', { name: 'crud group without ids' }));
    const bad2 = await api.createJob({ name: 'no schedule', task_type: 'pipeline_stop', pipeline_id: P });
    const clientErrors = {
      id: 'create-job-client-errors-answer-500',
      title: 'Job validation errors (HTTP 400 raised by JobService) are re-wrapped as HTTP 500 by the create route',
      severity: 'defect',
      detail: 'api/router.py create_job catches HTTPException in its generic `except Exception` and answers 500 "Error creating job: 400: ...". Clients cannot tell a rejected request from a server fault.',
      evidence: { groupStartWithoutGroupIds: [bad1.status, bad1.json], jobWithoutSchedule: [bad2.status, bad2.json] },
    };
    ctx.check('group_start without group_ids is rejected with 400', bad1.status === 400, `HTTP ${bad1.status} ${JSON.stringify(bad1.json)}`, { finding: clientErrors });
    ctx.check('job without schedule or cron is rejected with 4xx', bad2.status >= 400 && bad2.status < 500, `HTTP ${bad2.status} ${JSON.stringify(bad2.json)}`, { finding: clientErrors });
    const bad3 = await api.createJob({ name: 'bad type', task_type: 'no_such_task', cron_expression: '* * * * *', pipeline_id: P });
    ctx.check('unknown task_type is a clean 422, not a server error', bad3.status === 422, `HTTP ${bad3.status} ${JSON.stringify(bad3.json).slice(0, 200)}`, {
      finding: { id: 'validation-error-500-recursion', title: 'Request validation errors answer 500 "maximum recursion depth exceeded" instead of 422', severity: 'defect', detail: 'The jsonable_encoder override in core/app.py recurses forever while serialising the validation error (fixed on main, present in 0.2.2).', evidence: { status: bad3.status, body: bad3.json } },
    });
    const before = await api.listJobs();
    const bad4 = await api.createJob(jobSpec('pipeline_stop', { name: 'crud invalid cron', cron_expression: '61 25 * * *' }));
    const afterBad = await api.listJobs();
    ctx.check('invalid cron expression is rejected with 4xx', bad4.status >= 400 && bad4.status < 500, `HTTP ${bad4.status} ${JSON.stringify(bad4.json)?.slice(0, 200)}`, {
      finding: { ...clientErrors, evidence: { ...clientErrors.evidence, invalidCron: [bad4.status, bad4.json] } },
    });
    ctx.check('rejected job is not left behind in the database', afterBad.json?.total === before.json?.total, `jobs before ${before.json?.total}, after ${afterBad.json?.total}`, {
      finding: { id: 'failed-create-leaves-job-row', title: 'A job whose scheduling fails is committed anyway and survives as an enabled but never scheduled row', severity: 'defect', detail: 'JobService.create_job commits the row before scheduler_service.create_job runs, so an invalid cron expression answers an error while the job stays in the list (and fails to load on every startup).', evidence: { createStatus: bad4.status, totalBefore: before.json?.total, totalAfter: afterBad.json?.total } },
    });

    // ---- read
    const list = await api.listJobs();
    const total = list.json?.total;
    ctx.check('list returns items and total', list.status === 200 && Array.isArray(list.json?.items) && total === list.json.items.length, `total ${total}`);
    const byType = await api.listJobs({ task_type: 'pipeline_stop' });
    ctx.check('list filters by task_type', byType.json?.items?.every(j => j.task_type === 'pipeline_stop') && byType.json.items.length >= 1, `${byType.json?.items?.length} item(s)`);
    const byEnabled = await api.listJobs({ enabled: false });
    ctx.check('list filters by enabled=false', byEnabled.json?.items?.length >= 1 && byEnabled.json.items.every(j => j.enabled === false), `${byEnabled.json?.items?.length} item(s)`);
    const page = await api.listJobs({ skip: 1, limit: 1 });
    ctx.check('list paginates with skip and limit', page.json?.items?.length === 1 && page.json?.total === total, `items ${page.json?.items?.length} total ${page.json?.total}`);
    const got = await api.getJob(j1.id);
    ctx.check('get returns the job', got.status === 200 && got.json?.id === j1.id, `HTTP ${got.status}`);
    const missing = await api.getJob(999999);
    ctx.check('get unknown job returns 404', missing.status === 404, `HTTP ${missing.status}`);

    // ---- update
    const up = await api.put('update job', `/api/jobs/${j1.id}`, { name: 'crud entity start (renamed)', cron_expression: '0 6 * * 1-5', entity_ids: [E1] });
    ctx.check('update returns 200 with the new values', up.status === 200 && up.json?.name === 'crud entity start (renamed)' && up.json?.cron_expression === '0 6 * * 1-5' && JSON.stringify(up.json?.entity_ids) === JSON.stringify([E1]), `HTTP ${up.status} ${up.status === 200 ? '' : JSON.stringify(up.json)}`);
    ctx.check('update recalculates next_run', !!up.json?.next_run && up.json.next_run !== j1.next_run, `${j1.next_run} -> ${up.json?.next_run}`);
    const upType = await api.put('update task type', `/api/jobs/${j1.id}`, { task_type: 'entity_stop' });
    ctx.check('update can change the task type', upType.status === 200 && upType.json?.task_type === 'entity_stop', `HTTP ${upType.status}`);
    const upMissing = await api.put('update unknown job', '/api/jobs/999999', { name: 'x' });
    ctx.check('update unknown job returns 404', upMissing.status === 404, `HTTP ${upMissing.status}`);

    // ---- enable / disable
    const off = await api.setEnabled(j1.id, false);
    ctx.check('disable returns the job with enabled=false', off.status === 200 && off.json?.enabled === false, `HTTP ${off.status}`);
    const on = await api.setEnabled(j1.id, true);
    ctx.check('enable returns the job with enabled=true and a scheduler id', on.status === 200 && on.json?.enabled === true && /Scheduled job ID: job_/.test(on.json?.command || ''), `HTTP ${on.status} ${on.json?.command}`);

    // ---- roles (SUPER_ADMIN, MANAGER, MONITOR, VIEWER, anonymous)
    const asViewer = await api.createJob(jobSpec('pipeline_stop', { name: 'viewer job' }), { user: 'viewer' });
    ctx.check('viewer cannot create (403)', asViewer.status === 403, `HTTP ${asViewer.status}`);
    const asMonitor = await api.createJob(jobSpec('pipeline_stop', { name: 'monitor job' }), { user: 'monitor' });
    ctx.check('monitor cannot create (403)', asMonitor.status === 403, `HTTP ${asMonitor.status}`);
    const asManager = await api.createJob(jobSpec('pipeline_stop', { name: 'manager job' }), { user: 'manager' });
    ctx.check('manager can create (201)', asManager.status === 201, `HTTP ${asManager.status}`);
    const monitorRun = await api.runJob(asManager.json?.id, { user: 'monitor' });
    ctx.check('monitor can run a job now (200)', monitorRun.status === 200, `HTTP ${monitorRun.status}`);
    const monitorToggle = await api.setEnabled(asManager.json?.id, false, { user: 'monitor' });
    ctx.check('monitor can enable/disable (200)', monitorToggle.status === 200, `HTTP ${monitorToggle.status}`);
    const viewerRun = await api.runJob(asManager.json?.id, { user: 'viewer' });
    ctx.check('viewer cannot run a job (403)', viewerRun.status === 403, `HTTP ${viewerRun.status}`);
    const viewerList = await api.listJobs(undefined, { user: 'viewer' });
    ctx.check('viewer can list (200)', viewerList.status === 200, `HTTP ${viewerList.status}`);
    const monitorDelete = await api.deleteJob(asManager.json?.id, { user: 'monitor' });
    ctx.check('monitor cannot delete (403)', monitorDelete.status === 403, `HTTP ${monitorDelete.status}`);
    const anon = await api.listJobs(undefined, { anonymous: true });
    ctx.check('no credentials is 401', anon.status === 401, `HTTP ${anon.status}`);
    const garbage = await api.listJobs(undefined, { anonymous: true, headers: { cookie: 'gs-auth=not-a-token' } });
    ctx.check('unknown session token is 401', garbage.status === 401, `HTTP ${garbage.status}`);
    const bearer = await api.get('list with bearer token', '/api/jobs/', { anonymous: true, headers: { authorization: `Bearer ${(await (await import('../lib/stack.mjs')).userToken(api.inst, 'admin'))}` } });
    ctx.check('Bearer Authorization header works like the cookie', bearer.status === 200, `HTTP ${bearer.status}`);

    // ---- delete
    const del = await api.deleteJob(j1.id);
    ctx.check('delete returns 204', del.status === 204, `HTTP ${del.status}`);
    const gone = await api.getJob(j1.id);
    ctx.check('deleted job is gone (404)', gone.status === 404, `HTTP ${gone.status}`);
    const delMissing = await api.deleteJob(999999);
    ctx.check('delete unknown job returns 404', delMissing.status === 404, `HTTP ${delMissing.status}`);
  },
};
