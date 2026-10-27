import { containerLogs } from '../lib/stack.mjs';

export default {
  name: 'internal-api',
  description: 'The internal /api/pipelines/* control API (meant for localhost cron calls only): who can reach it without credentials',
  async run(ctx) {
    const { inst, api } = await ctx.resetMain();
    ctx.usedMain = true;
    const since = new Date().toISOString();
    const mark = await ctx.mark();
    const r = await api.post('pause pipeline without credentials', '/api/pipelines/pl-extra/pause', {}, { anonymous: true });
    const calls = await ctx.calls(mark, { tag: 'chronos', only: /commands\/sync\/stop/ });
    const logs = await containerLogs(inst.name, { since });
    const peer = (logs.match(/(\d+\.\d+\.\d+\.\d+):\d+ - "POST \/api\/pipelines\/pl-extra\/pause/) || [])[1];
    const evidence = { request: 'POST /api/pipelines/pl-extra/pause (no cookie, no token) from the Docker host', httpStatus: r.status, chronosSawPeer: peer, coreHubCalls: ctx.describe(calls) };
    ctx.check('the internal pipeline API refuses callers that are not on localhost', r.status === 403 && calls.length === 0, `HTTP ${r.status}, peer ${peer}, Core Hub saw ${calls.length} command(s)`, {
      finding: {
        id: 'internal-pipeline-api-open-to-private-ranges',
        title: 'The internal pipeline control API accepts any caller from 172.* and 192.168.* without authentication',
        severity: 'defect',
        detail: 'verify_localhost (api/pipeline_router.py) lets through every client address starting with "172." or "192.168." so that Docker networking works. Any container on the Docker network, any LAN host in 192.168.0.0/16 and any 172.* address (the check is a string prefix, so it also matches public 172.x addresses) can start, stop, snapshot, redo and put pipelines in maintenance through Chronos with no cookie and no role check.',
        evidence,
      },
      evidence,
    });
  },
};
