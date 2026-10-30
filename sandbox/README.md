# Chronos sandbox

Runs the **real Chronos** against a **fake Core Hub** with a wall clock the sandbox controls. It exercises every function Chronos has (task types, jobs, chains, trigger flows, settings, Query Studio, AI agent runs, ...), injects Core Hub faults, and replays DST nights so that failures such as GSSD-1457 can be reproduced and measured instead of guessed at.

Nothing here changes Chronos: the sandbox observes. A failing check is either a Chronos defect (reported as a finding with evidence) or a sandbox problem (reported as a runner error).

## Quick start

```bash
cd sandbox
node run.mjs up                                 # build images, start fake Core Hub + real Chronos
node run.mjs build                              # only build the Chronos image (CI does this once per pipeline)
node run.mjs list                               # scenarios
node run.mjs scenario jobs-crud task-types      # a few scenarios
node run.mjs all                                # every scenario (long: DST scenarios run in real time)
                                                # exit status: 0 unless something beyond known-findings.json is wrong
node run.mjs status
node run.mjs down [--purge]                     # remove containers, volumes, network (run/ and images with --purge)
```

Only Node (24) and Docker are needed; there are no npm dependencies. The first `up` builds the Chronos image from the repo `Dockerfile` (a few minutes); later runs reuse it.

Flags (also accepted by `scenario` and `all`; pass the same ones every time, the sandbox is recreated when they change):

| Flag | Meaning |
|---|---|
| `--image repo` (default) | build Chronos from this repo's `Dockerfile` |
| `--image molo17/gluesync-chronos:0.2.2` | use a published image (the customer runs 0.2.2) |
| `--apscheduler 3.11.3` | `pip install apscheduler==<version>` in the overlay image, to compare behaviour with the version the repo pins (3.11.3 on this branch) |
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
| `upgrade-from-ga` | GSSD-1457 upgrade from the published GA image: `next_run` converted when the GA predates the fix, else the marker is already there; runs missed during the upgrade replayed | 3 min |
| `upgrade-from-pre-utc` | GSSD-1457 conversion: upgrade from 0.3.1, the last release that stored `next_run` as wall clock; the rows are converted and the missed runs replayed | 3 min |

`upgrade-from-ga` is the upgrade path of GSSD-1457. It starts the published GA image (`SANDBOX_UPGRADE_FROM=<tag>`, else `latestVersionGA` of `https://api.backoffice.molo17.com/agent/chronos/version`; it fails with a message when neither resolves) with libfaketime added (`ensureChronosOverlay` in `lib/stack.mjs`, overlay `chronos-sandbox/chronos:ga-<tag>`; with `SANDBOX_IMAGE_MIRROR` the base comes from `<mirror>/gluesync-chronos:<tag>`), creates a job due at 14:30, one exit-maintenance due at 14:35 and one at 18:00 (Europe/Rome, in June), stops it before 14:25Z and starts the build under test on the same data volume at 14:50 Rome. It checks that both missed runs execute exactly once, that the stored `next_run` values were converted (the 18:00 job is still due at 16:00Z) and that the migration logged the line expected for that release: `Converted next_run to UTC` when it is older than 0.3.2, `next_run already stored as UTC, skipping` from 0.3.2 on (those releases write the marker themselves). Since every GA from 0.3.2 on skips the conversion, `upgrade-from-pre-utc` runs the same steps from 0.3.1 (`SANDBOX_UPGRADE_FROM_PRE_UTC=<tag>` to change it), so the conversion stays covered. The GA API shows the stored wall clock as if it were UTC (`16:30+02:00` for the 14:30 job), so its `next_run` is not compared. Run with `--image molo17/gluesync-chronos:0.1.67` (pre-fix) the catch-up, `next_run` and migration checks fail.

`jobs-crud` to `corehub-slow` assert against the repo build. Older images differ (0.2.2 lacks `fromScratch=true` on snapshots, GSSD-1346, and the five newest task types), and those differences show up as failed checks.

## Results (GSSD-1457, 26-27 Oct 2026)

**On this branch (`main` plus the bump to APScheduler 3.11.3, the GSSD-1457 fix) the repo build no longer freezes and no longer pauses across the spring-forward.** Today's repo-image run: `dst-fallback-freeze` (the API, the every-minute job keep going past 02:59 CEST), `dst-fallback-customer` phases A and B, `dst-springforward` and all 28 cells of `dst-fallback-patterns` pass. What the sandbox still reports there is in `known-findings.json`: the restart catch-up defects (`GSSD-1457-missed-runs-lost-on-restart`, `next-run-left-in-past-after-restart`): on the customer night the missed 03:10 exit-maintenance jobs are still not run after a restart, so both pipelines stay in maintenance, with or without the freeze. The paragraphs below are the measurements on the **customer's image 0.2.2 and on the repo build before the bump** (APScheduler 3.11.0), which is where the findings come from.

Customer image `molo17/gluesync-chronos:0.2.2` (APScheduler 3.11.0, Python 3.14) unless stated. Reports are git-ignored; the numbers below are from the runs in `reports/` on the machine that ran them.

**Fall-back freeze (25 Oct 2026, Europe/Rome).** `dst-fallback-customer` reproduces the ticket: both pipelines enter maintenance at 01:50 CEST, Chronos freezes at 02:59 CEST with no error in the log (py-spy: `CronTrigger.get_next_fire_time` <- `Job._get_run_times`, one CPU at 100%), and after the 06:03 CET restart the missed 03:10 exit jobs are not run but moved to 1 November, so both pipelines stay in maintenance. The API `next_run` is wrong on that night too: after the restart the exit jobs still show 25 Oct 03:10, and the 01:50 enter jobs show 02:50.

`dst-fallback-patterns` (who freezes):

| Cron | Scheduler TZ Europe/Rome | Scheduler TZ UTC, job TZ Rome | Scheduler TZ UTC, no job TZ |
|---|---|---|---|
| `* * * * *`, `*/5 * * * *` | freezes | freezes | ok |
| `0 * * * *`, `30 2 * * *`, `59 2 * * *` | freezes | ok | ok |
| `50 1 * * 0`, `10 3 * * 0` (the customer's maintenance jobs) | ok | ok | ok |

The customer's freeze came from their frequent job, not from the maintenance jobs. The same image with `--apscheduler 3.11.3` does not freeze: the every-minute job, the API and the Core Hub pings keep going past 02:59 CEST. The repo build before the bump (`main` with APScheduler 3.11.0) froze like 0.2.2; with the bump to 3.11.3 it does not.

**Spring-forward (29 Mar 2026).** No freeze, but the every-minute job stops for an hour (last run 01:59 CET, none until after 03:03 CEST): across the gap a job due one minute later is computed as due in 3660 s, because same-zone aware subtraction uses wall-clock time. Fixed by APScheduler 3.11.3 (the repo build no longer shows it).

**Restart catch-up** (still present on the bumped build). A run whose fire time passes while Chronos is down is never executed after the restart; jobs are re-created from "now" and the API keeps the past `next_run`.

**Health check.** This branch adds `GET /health` and an image `HEALTHCHECK`. On the repo build, `dst-fallback-freeze --keep` froze at 02:58:59 CEST and Docker marked the container `unhealthy` (every probe "timed out", failing streak 9); `docker restart` brought it back `healthy` with `{"status":"ok"}` at 02:05 CET. Plain Docker only marks the container, it does not restart it; Swarm does.

**Other findings** (fast scenarios on the repo build, still present on the bumped one):

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

`junit.xml` has one testsuite per scenario and one testcase per check (see "Known findings" below for how findings are reported).

## Known findings and the exit status

The sandbox finds real defects, so on today's code many checks fail. `known-findings.json` lists the findings accepted for now, each with the scenarios that report it and a one-line note:

```json
{ "findings": { "no-retry-on-corehub-5xx": { "scenarios": ["corehub-faults"], "note": "..." } } }
```

`run.mjs scenario|all` (locally and in CI, same rule) exits non-zero when any of these holds:

- a scenario ended with a **runner error**;
- a `setup:` check failed (the sandbox could not arrange the situation: a sandbox problem, not a verdict on Chronos);
- a check failed **without a finding** (also a sandbox or scenario problem);
- a check failed with a finding that is **not in `known-findings.json`** (a new defect or a regression);
- a known finding **no longer occurs** in a scenario that ran (stale entry: fixed? remove it from `known-findings.json`).

A check that fails with a known finding does not fail the run, and is not hidden: `report.md` marks it `known` (and the finding `known`, with its note), and `junit.xml` reports it as **skipped** with the finding and note as the message, so the pipeline's test report lists the accepted defects as skipped tests. New findings and checks without a finding are `failure`, `setup:` checks and runner errors are `error`, and a stale entry is a failing testcase of its own (`known finding <id> still occurs`). `report.json` carries the same under `gate`.

The list describes the **repo build** (`--image repo`, the default). With `--image <published image>` or `--apscheduler` it is not applied, every failed check counts, and stale entries are not looked for; a run restricted with `--patterns` does not look for stale entries either (a subset of a matrix cannot say that a finding is gone).

Workflow: a Chronos fix makes its finding disappear, the stale entry fails the next run, and the fix's merge request removes the entry. A new finding (a failing pipeline) is either fixed or, if it is accepted for now, added with a note by the merge request that introduces it.

## The fake clock and its limits

- Offsets are fixed per container (`FAKETIME=<seconds>`); never use absolute local times. libfaketime parses `@YYYY-MM-DD HH:MM:SS` in the process time zone and takes the later fold of an ambiguous time, so 02:45 on 2026-10-25 in Europe/Rome would silently become CET.
- Run at x1. Acceleration (`x20`) speeds up the wall clock but not `CLOCK_MONOTONIC`, so asyncio and APScheduler timers fire late in fake time. DST scenarios therefore start a couple of minutes before the instant that matters and wait in real time.
- Docker start latency varies (the offset is aimed with a moving estimate of it). A scenario that becomes ready too close to its trigger instant stops with a `setup:` check instead of producing a misleading result.
- The customer night is 70 minutes between 01:50 and 02:59. `dst-fallback-customer` replays it in three container incarnations on the same data volume (A 01:48 CEST, B 02:55 CEST, C 06:03 CET) instead of waiting.
- Chronos needs `--cap-add SYS_PTRACE` for `py-spy dump`; the sandbox adds it.
- py-spy does not support Python 3.15 yet, and the repo `Dockerfile` floats to the newest Python 3. On such an image the stack check becomes a note ("py-spy cannot read this image") instead of a failed check.

## In CI

Two jobs in `.gitlab-ci.yml`, on merge request pipelines only (never manual), on the `hq-linux-docker` runners, both `interruptible`:

- **`sandbox_image`** (stage `sandbox-build`, starts at once, next to the unit tests): `sh ci/sandbox-job.sh build` builds the Chronos image once (repo `Dockerfile` plus the `chronos/Dockerfile` overlay, `node run.mjs build`) and keeps `docker save | gzip` of it for one day as `sandbox/image/chronos-sandbox-image.tar.gz`.
- **`sandbox`** (`parallel: 4`, 60 min timeout): `docker load`s that image, so `up` reuses it instead of building it four times (`up` builds only what is missing; the fake Core Hub image is rebuilt in each shard, a few seconds), then runs the shard's scenarios in one report. Reports, with `junit.xml` (the pipeline's test report), are kept for two weeks.

| Shard | Scenarios | Real time |
|---|---|---|
| 1 | `jobs-crud task-types chains triggers webhooks internal-api settings query-studio ai-agent core-hub-tasks maintenance corehub-faults` | ~15 min |
| 2 | `corehub-outage corehub-slow restart-catchup upgrade-from-ga upgrade-from-pre-utc` | ~13 min |
| 3 | `dst-fallback-freeze dst-fallback-customer dst-springforward` | ~28 min |
| 4 | `dst-fallback-patterns --concurrency 6` | 30-35 min |

The lists live in `ci/sandbox-job.sh`; change them there (and the budgets next to them). The times are measured with the image loaded (local run in the CI image); the budgets leave room for a slower runner. Each phase has a time budget (`SANDBOX_UP_BUDGET` 600 s, the shard budgets 1500 / 1500 / 2100 / 2700 s, `SANDBOX_TEARDOWN_BUDGET` 120 s, `SANDBOX_BUILD_BUDGET` 1500 s) enforced with `timeout`, so a hang fails the job with a message (`... HUNG: killed after Ns`) well before the 60 minutes. `dst-fallback-patterns` runs 6 cells at a time (`SANDBOX_PATTERNS_CONCURRENCY`); a frozen Chronos burns one CPU, so a runner with fewer cores than that should lower it, and the scenario times grow accordingly.

- **Own daemon.** Each job runs in a privileged container (the CI image below) and starts its own `dockerd` there (data root on the image's `/var/lib/docker` volume, never overlay on overlay; the script refuses `vfs`). Concurrent pipelines never share a daemon, so there are no name or port clashes, and the bind-mount paths under the checkout exist for the daemon that mounts them. A trap tears the sandbox down (`down --purge`) whatever the outcome.
- **Known findings.** The exit status is the one described under "Known findings and the exit status" above: today's defects pass, anything new fails, and so does a known finding that has disappeared.
- **No signing, no registry credentials**: nothing leaves the jobs except the artifacts.
- **Nothing from Docker Hub.** The job image is `nexus.hq.molo17.com/molo17-local/chronos-sandbox-ci:1` (`ci/sandbox-ci.Dockerfile`): `docker:28.3.3-dind` plus Node 24 and the `ws` package the fake Core Hub needs (in `/opt/sandbox-deps`; `lib/stack.mjs` stages it into the fake's build context, otherwise the Dockerfile runs `npm install`). `ci/sandbox-job.sh` fails if Node is not 24. The sandbox's own base images come from Nexus through `SANDBOX_IMAGE_MIRROR` (below). Building the Chronos image still downloads what its Dockerfiles always do: the Chainguard base (`cgr.dev`), PyPI (and the GitLab package index for `gluesync-sdk`), and the Alpine/Wolfi package repositories.

### Image mirror (`SANDBOX_IMAGE_MIRROR`)

Unset (a developer's Mac), every image comes from Docker Hub on first use. Set it to a registry/repository, e.g. `nexus.hq.molo17.com/molo17-local` (the `sandbox` job does), and `up` pulls `<mirror>/<name:tag>` instead and tags it with the canonical Docker Hub name before anything uses it (`seedImages` in `lib/stack.mjs`; the one list is `requiredImages` in `lib/env.mjs`). An image that is already present is not pulled again. Because the names are canonical and present, `docker build` (no `--pull`) uses the local image for `FROM` without contacting a registry, so no Dockerfile needed changes.

| Image | Used for |
|---|---|
| `node:24.16.0-alpine` | `FROM` of `fakes/Dockerfile` (the fake Core Hub) |
| `alpine:3.20` | the throw-away container that generates the sandbox CA and the PKCS12 keystore |

The mirror must hold every name above with the same tag (check with `docker manifest inspect <mirror>/<name>`); mirror a new image there before adding it to `requiredImages`.

### The CI image

Rebuild and push it when `ci/sandbox-ci.Dockerfile` changes, or the `ws` version in `fakes/Dockerfile` does (the build fails if the two differ), and bump the tag (`:1` is the current one) in the Dockerfile header, in `.gitlab-ci.yml` and here. From the repository root, logged in to Nexus:

```bash
docker buildx build --platform linux/amd64,linux/arm64 --provenance=false --sbom=false \
  -f ci/sandbox-ci.Dockerfile --build-arg SOURCE_COMMIT=$(git rev-parse --short HEAD) \
  -t nexus.hq.molo17.com/molo17-local/chronos-sandbox-ci:1 --push .
docker buildx imagetools inspect nexus.hq.molo17.com/molo17-local/chronos-sandbox-ci:1
```

(Alpine 3.22 has no Node 24 package, so the image copies the `node` binary from the pinned `node:24.16.0-alpine`, also from Nexus; the build fails if `node --version` is not v24.)

### Reproducing a shard locally

In the same image and with the same mirror, from a copy of the checkout (the container runs its own `dockerd`, so it never touches a sandbox you have running on the host). Shard 3 of 4; the build step is only needed once, and `sandbox_image`'s artifact is replaced by a local build when `sandbox/image` is absent (the shard then builds the image itself):

```bash
docker run --rm --privileged --entrypoint sh \
  -v "$PWD":/src:ro -v /tmp/sandbox-out:/out \
  -e CI_NODE_INDEX=3 -e CI_NODE_TOTAL=4 \
  -e SANDBOX_IMAGE_MIRROR=nexus.hq.molo17.com/molo17-local \
  nexus.hq.molo17.com/molo17-local/chronos-sandbox-ci:1 -c '
    mkdir -p /builds/chronos && cd /builds/chronos
    tar -C /src --exclude=./.git --exclude=./sandbox/run --exclude=./sandbox/reports -cf - . | tar -xf -
    sh ci/sandbox-job.sh; rc=$?
    cp -a sandbox/reports/. /out/
    exit $rc'
```

To run other scenarios in that environment, start the container with `sh -c 'dockerd-entrypoint.sh dockerd >/var/log/dockerd.log 2>&1 & sleep 15; ...'` and `cd sandbox && node run.mjs scenario <names>`, or edit the lists in `ci/sandbox-job.sh`. `SANDBOX_SCENARIOS_BUDGET` overrides the shard's time budget.

## Layout

```
run.mjs                   CLI
lib/                      env, util, stack (docker, fake admin client), chronos (REST client), context (checks), runner, gate (pass/fail rule), report (report.md, report.json, junit.xml)
known-findings.json       findings accepted for now (see "Known findings and the exit status")
fakes/                    fake-corehub.mjs and its Dockerfile
chronos/Dockerfile        overlay: libfaketime, py-spy, optional apscheduler
scenarios/                one file per scenario, index.mjs lists them
reports/ run/ image/      output (git-ignored); image/ is the CI hand-over of the Chronos image
ci/sandbox-job.sh, ci/sandbox-ci.Dockerfile   (repo root) what the CI jobs run, and their image
```
