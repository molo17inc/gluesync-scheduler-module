# Chronos sandbox

Runs the **real Chronos** against a **fake Core Hub** with a wall clock the sandbox controls. It exercises every function Chronos has (task types, jobs, chains, trigger flows, settings, Query Studio, AI agent runs, ...), injects Core Hub faults, and replays DST nights so that failures such as GSSD-1457 can be reproduced and measured instead of guessed at.

Nothing here changes Chronos: the sandbox observes. A failing check is either a Chronos defect (reported as a finding with evidence) or a sandbox problem (reported as a runner error).

## Quick start

```bash
cd sandbox
node run.mjs up                                 # build images, start fake Core Hub + real Chronos
node run.mjs list                               # scenarios
node run.mjs scenario jobs-crud task-types      # a few scenarios
node run.mjs all                                # every scenario (long: DST scenarios run in real time)
node run.mjs status
node run.mjs down [--purge]                     # remove containers, volumes, network (run/ and images with --purge)
```

Only Node (24) and Docker are needed; there are no npm dependencies. The first `up` builds the Chronos image from the repo `Dockerfile` (a few minutes); later runs reuse it.

Flags (also accepted by `scenario` and `all`; pass the same ones every time, the sandbox is recreated when they change):

| Flag | Meaning |
|---|---|
| `--image repo` (default) | build Chronos from this repo's `Dockerfile` |
| `--image molo17/gluesync-chronos:0.2.2` | use a published image (the customer runs 0.2.2) |
| `--apscheduler 3.11.3` | `pip install apscheduler==<version>` in the overlay image, to compare behaviour with the pinned 3.11.0 |
| `--name`, `--port-base` | project name and host ports; also `SANDBOX_NAME` (default `chronos-sandbox`) and `SANDBOX_PORT_BASE` (default `5417`) |
| `--keep` | leave scenario containers running for inspection |
| `--concurrency N`, `--patterns "a;b"` | `dst-fallback-patterns` only: cells at a time, and a subset of cron patterns |
| `--no-tls` | plain HTTP/WS between Chronos and the fake (default is TLS with a sandbox CA and a PKCS12 keystore) |

Two sandboxes can run side by side on one Mac, for example `SANDBOX_NAME=chronos-b SANDBOX_PORT_BASE=5517 node run.mjs ...`. Every container, volume and network carries the project name, and `down` only removes what carries it.

## What is real and what is fake

| Piece | In the sandbox |
|---|---|
| Chronos | **Real**: the repo build or any published image, plus an overlay (`chronos/Dockerfile`) that adds libfaketime, py-spy and an optional `apscheduler` version. Application code is untouched. |
| Core Hub | Fake (`fakes/fake-corehub.mjs`, Node): WSS `/ext-module` handshake, token issue, ping/pong; `/authentication/login|me|logout`; pipelines, entities, groups, entity status, sync commands, maintenance commands; `/global-config/webhooks` with real callbacks to Chronos; Query Studio, AI agent runs, brain job, data comparison (Validator), Visualize refresh. State is in memory, so commands have effects: a sync command on a pipeline in maintenance answers `406 Pipeline with id X is in maintenance. Please wait until the maintenance mode is disabled.` |
| Clock | Real time shifted by libfaketime (`LD_PRELOAD`, `FAKETIME=<offset seconds>`, monotonic clock left alone). The offset is computed at container start so that the fake clock reads the target time once Chronos is ready. |
| Users | The fake issues session tokens for `admin` (SUPER_ADMIN), `manager`, `monitor`, `viewer` (password = name). The orchestrator sends them in the `gs-auth` cookie like the UI, and Chronos introspects them through the fake's `/authentication/me`. |
| TLS | A sandbox CA signs one certificate used by the fake, and a PKCS12 file (`security-config.json`) that Chronos uses for its own HTTPS and for the SDK. |

## Ports (localhost only)

| Port (default base 5417) | What |
|---|---|
| base | fake Core Hub (HTTPS or HTTP with `--no-tls`) |
| base + 1 | main Chronos instance (REST API, `https://127.0.0.1:5418/api/docs`) |
| base + 2 | fake Core Hub admin API (`x-sandbox-admin` header, plain HTTP; stays up while Core Hub is "down") |

Scenarios that need their own Chronos (clock, time zone, restarts) start containers on random free host ports.

## Scenarios

| Scenario | What it does | Real time |
|---|---|---|
| `jobs-crud` | create (cron and `schedule` forms), validation, filters, pagination, update, enable/disable, delete, role permissions | 1 min |
| `task-types` | every `TaskType` (19): create, run now, assert the exact ordered Core Hub calls and the model effect | 1 min |
| `chains` | async and sync chained events (Core Hub webhook wait), webhook lifecycle, failure and timeout, group events | 2 min |
| `triggers` | trigger flows: CRUD, fire by token, fire from the UI, token rotation, platform-event flows, origin and broadcast routing, logs | 2 min |
| `webhooks` | `/api/webhooks/*` header validation and who can reach them | 1 min |
| `internal-api` | the localhost-only `/api/pipelines/*` control API and who can reach it | <1 min |
| `settings` | settings API, timezone validation, effect on new jobs, roles | <1 min |
| `query-studio` | agent list, read-only guard, writable queries, saved query live SQL and fallback | 1 min |
| `ai-agent` | agent run POST, polling, failure, approval wait, validation | 1 min |
| `core-hub-tasks` | entity validation (differences, reconcile, failure), brain reindex, Visualize refresh (404, 4xx, 5xx retry) | 1 min |
| `maintenance` | enter/exit jobs and what happens to sync jobs refused with 406 while a pipeline is in maintenance | 1 min |
| `corehub-faults` | 503, dropped connection, slow answer, introspection failing, revoked module token | 2 min |
| `corehub-outage` | Core Hub down 70 s, dropped WebSocket: how long Chronos needs to recover | 6-9 min |
| `corehub-slow` | a command answered after 135 s, longer than the 120 s internal timeout | 3 min |
| `dst-fallback-freeze` | GSSD-1457: every-minute job plus the customer's maintenance jobs, TZ Europe/Rome, clock 02:55:30 CEST on 2026-10-25 | 6 min |
| `dst-fallback-customer` | GSSD-1457 replay: 01:50 enter / 03:10 exit maintenance on two pipelines, an every-minute job, freeze at 02:59 CEST, restart at 06:03 CET | 12 min |
| `dst-fallback-patterns` | matrix: 7 cron patterns x scheduler TZ (Europe/Rome, UTC) x job timezone (Europe/Rome, unset), one container per cell | 20-30 min |
| `dst-springforward` | 2026-03-29 with `30 2 * * *` and an every-minute job | 6 min |
| `restart-catchup` | a daily job misses its fire time while Chronos is down | 3 min |

`jobs-crud` to `corehub-slow` assert against the repo build. Older images differ (0.2.2 lacks `fromScratch=true` on snapshots, GSSD-1346, and the five newest task types), and those differences show up as failed checks.

## Results (GSSD-1457, 26-27 Oct 2026)

Customer image `molo17/gluesync-chronos:0.2.2` (APScheduler 3.11.0, Python 3.14) unless stated. Reports are git-ignored; the numbers below are from the runs in `reports/` on the machine that ran them.

**Fall-back freeze (25 Oct 2026, Europe/Rome).** `dst-fallback-customer` reproduces the ticket: both pipelines enter maintenance at 01:50 CEST, Chronos freezes at 02:59 CEST with no error in the log (py-spy: `CronTrigger.get_next_fire_time` <- `Job._get_run_times`, one CPU at 100%), and after the 06:03 CET restart the missed 03:10 exit jobs are not run but moved to 1 November, so both pipelines stay in maintenance. The API `next_run` is wrong on that night too: after the restart the exit jobs still show 25 Oct 03:10, and the 01:50 enter jobs show 02:50.

`dst-fallback-patterns` (who freezes):

| Cron | Scheduler TZ Europe/Rome | Scheduler TZ UTC, job TZ Rome | Scheduler TZ UTC, no job TZ |
|---|---|---|---|
| `* * * * *`, `*/5 * * * *` | freezes | freezes | ok |
| `0 * * * *`, `30 2 * * *`, `59 2 * * *` | freezes | ok | ok |
| `50 1 * * 0`, `10 3 * * 0` (the customer's maintenance jobs) | ok | ok | ok |

The customer's freeze came from their frequent job, not from the maintenance jobs. The same image with `--apscheduler 3.11.3` does not freeze: the every-minute job, the API and the Core Hub pings keep going past 02:59 CEST. The repo build (`main`, still APScheduler 3.11.0) freezes like 0.2.2.

**Spring-forward (29 Mar 2026).** No freeze, but the every-minute job stops for an hour (last run 01:59 CET, none until after 03:03 CEST): across the gap a job due one minute later is computed as due in 3660 s, because same-zone aware subtraction uses wall-clock time. Also fixed upstream in APScheduler 3.11.3.

**Restart catch-up.** A run whose fire time passes while Chronos is down is never executed after the restart; jobs are re-created from "now" and the API keeps the past `next_run`.

**Health check.** This branch adds `GET /health` and an image `HEALTHCHECK`. On the repo build, `dst-fallback-freeze --keep` froze at 02:58:59 CEST and Docker marked the container `unhealthy` (every probe "timed out", failing streak 9); `docker restart` brought it back `healthy` with `{"status":"ok"}` at 02:05 CET. Plain Docker only marks the container, it does not restart it; Swarm does.

**Other findings** (fast scenarios on the repo build):

- `jobs-crud`: validation errors (HTTP 400) answer 500; a job whose scheduling fails is committed anyway and stays enabled but never scheduled.
- `chains`: an async chained event after the last awaited step never runs; `group_start` / `group_stop` act on the whole pipeline.
- `maintenance`: the 406 "pipeline in maintenance" reason is only in the log, not in the job error.
- `corehub-faults`: one transient Core Hub 5xx fails a scheduled command for good (no retry); the 401 recovery reconnects the SDK on a throw-away event loop, so the WebSocket dies right after the retried call.
- `webhooks`: `POST /api/webhooks/platform-event` fires any enabled trigger flow for a caller that only sends two static headers.
- `internal-api`: the pipeline control API accepts any caller from 172.* and 192.168.* without authentication.

## Reading a report

Each run writes `reports/<timestamp>-<scenarios>/`:

- `report.md`: settings, a summary table, **Findings** (real Chronos defects and observations, each with evidence), then one section per scenario with every check
- `report.json`: the same, machine readable
- `run.log`: the console output
- `<scenario>/`: `api.json` (every REST call the orchestrator made and the answer), `corehub-calls.json` (every request and WebSocket event the fake Core Hub saw, with real UTC time and, for the module under test, the Chronos fake-clock time in `fakeTs`), `corehub-ws-pings.json`, `chronos-*.log` (container logs per incarnation), `checks.json`, plus scenario evidence such as `py-spy-dump.txt`, `*-timeline.json`, `matrix.md`

A check that fails because Chronos misbehaves carries a finding; a check that fails because the sandbox could not set something up is named `setup:` or ends the scenario with a runner error.

## The fake clock and its limits

- Offsets are fixed per container (`FAKETIME=<seconds>`); never use absolute local times. libfaketime parses `@YYYY-MM-DD HH:MM:SS` in the process time zone and takes the later fold of an ambiguous time, so 02:45 on 2026-10-25 in Europe/Rome would silently become CET.
- Run at x1. Acceleration (`x20`) speeds up the wall clock but not `CLOCK_MONOTONIC`, so asyncio and APScheduler timers fire late in fake time. DST scenarios therefore start a couple of minutes before the instant that matters and wait in real time.
- Docker start latency varies (the offset is aimed with a moving estimate of it). A scenario that becomes ready too close to its trigger instant stops with a `setup:` check instead of producing a misleading result.
- The customer night is 70 minutes between 01:50 and 02:59. `dst-fallback-customer` replays it in three container incarnations on the same data volume (A 01:48 CEST, B 02:55 CEST, C 06:03 CET) instead of waiting.
- Chronos needs `--cap-add SYS_PTRACE` for `py-spy dump`; the sandbox adds it.
- py-spy does not support Python 3.15 yet, and the repo `Dockerfile` floats to the newest Python 3. On such an image the stack check becomes a note ("py-spy cannot read this image") instead of a failed check.

## Layout

```
run.mjs                   CLI
lib/                      env, util, stack (docker, fake admin client), chronos (REST client), context (checks), runner, report
fakes/                    fake-corehub.mjs and its Dockerfile
chronos/Dockerfile        overlay: libfaketime, py-spy, optional apscheduler
scenarios/                one file per scenario, index.mjs lists them
reports/ run/             output (git-ignored)
```
