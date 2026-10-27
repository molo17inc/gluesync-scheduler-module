import { jobSpec } from '../lib/chronos.mjs';

export default {
  name: 'settings',
  description: 'Settings API: list, get, update the timezone (valid/invalid), create, duplicate, roles, and the effect of the timezone on new jobs',
  async run(ctx) {
    const { api } = await ctx.resetMain();
    ctx.usedMain = true;

    const list = await api.get('list settings', '/api/settings/');
    const tz = list.json?.items?.find(s => s.key === 'timezone');
    ctx.check('settings list contains the timezone, initialised from TZ', list.status === 200 && tz?.value === 'Europe/Rome', `HTTP ${list.status} timezone=${tz?.value}`);
    const one = await api.get('get timezone', '/api/settings/timezone');
    ctx.check('get a setting by key', one.status === 200 && one.json?.key === 'timezone', `HTTP ${one.status}`);
    const missing = await api.get('get unknown setting', '/api/settings/nope');
    ctx.check('unknown setting is 404', missing.status === 404, `HTTP ${missing.status}`);

    const bad = await api.put('invalid timezone', '/api/settings/timezone', { value: 'Mars/Olympus' });
    ctx.check('invalid timezone is rejected with 400', bad.status === 400, `HTTP ${bad.status}`);
    const asViewer = await api.put('viewer update', '/api/settings/timezone', { value: 'UTC' }, { user: 'viewer' });
    ctx.check('viewer cannot change settings (403)', asViewer.status === 403, `HTTP ${asViewer.status}`);
    const asMonitor = await api.put('monitor update', '/api/settings/timezone', { value: 'UTC' }, { user: 'monitor' });
    ctx.check('monitor cannot change settings (403)', asMonitor.status === 403, `HTTP ${asMonitor.status}`);

    const upd = await api.put('manager update', '/api/settings/timezone', { value: 'Asia/Tokyo' }, { user: 'manager' });
    ctx.check('manager can change the timezone (200)', upd.status === 200 && upd.json?.value === 'Asia/Tokyo', `HTTP ${upd.status}`);
    const job = await api.createJob(jobSpec('pipeline_stop', { name: 'settings tz job', cron_expression: '30 9 * * *' }));
    ctx.check('new jobs take the timezone setting', job.json?.timezone_name === 'Asia/Tokyo' && /\+09:00$/.test(job.json?.next_run || ''), `timezone_name=${job.json?.timezone_name} next_run=${job.json?.next_run}`);
    const restored = await api.put('restore timezone', '/api/settings/timezone', { value: 'Europe/Rome' });
    ctx.check('timezone restored', restored.status === 200, `HTTP ${restored.status}`);
    const stale = await api.getJob(job.json?.id);
    ctx.check('existing jobs keep the timezone they were created with', stale.json?.timezone_name === 'Asia/Tokyo', `timezone_name=${stale.json?.timezone_name}`);

    const created = await api.post('create setting', '/api/settings/', { key: 'sandbox_note', value: 'hello', description: 'sandbox' });
    ctx.check('create a setting (201)', created.status === 201 && created.json?.key === 'sandbox_note', `HTTP ${created.status}`);
    const dup = await api.post('duplicate setting', '/api/settings/', { key: 'sandbox_note', value: 'again' });
    ctx.check('duplicate key is 409', dup.status === 409, `HTTP ${dup.status}`);
    const unknownPut = await api.put('update unknown key', '/api/settings/other', { value: 'x' });
    ctx.check('updating an unknown key is 404', unknownPut.status === 404, `HTTP ${unknownPut.status}`);
    const viewerPost = await api.post('viewer create', '/api/settings/', { key: 'x', value: 'y' }, { user: 'viewer' });
    ctx.check('viewer cannot create settings (403)', viewerPost.status === 403, `HTTP ${viewerPost.status}`);
  },
};
