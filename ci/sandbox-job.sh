#!/bin/sh
# The Chronos sandbox in CI. Run by the jobs of .gitlab-ci.yml, and by hand to reproduce one (see
# "In CI" in sandbox/README.md):
#
#   sh ci/sandbox-job.sh build    the `sandbox_image` job: builds the Chronos image the scenarios run
#                                 against once, and saves it as an artifact
#   sh ci/sandbox-job.sh          the `sandbox` job: one shard of the scenarios, picked by
#                                 CI_NODE_INDEX / CI_NODE_TOTAL (4 shards, listed below)
#
# It expects what the jobs have: a privileged container from chronos-sandbox-ci (docker:28.3.3-dind
# plus Node 24 and `ws`, see ci/sandbox-ci.Dockerfile; alpine, busybox sh) and the repository checked
# out. The container runs its own dockerd, so concurrent jobs never share a daemon and the sandbox's
# bind-mount paths (under the checkout) exist for the daemon that mounts them.
#
# Nothing is downloaded from Docker Hub: with SANDBOX_IMAGE_MIRROR set the sandbox pulls its base images
# (sandbox/lib/env.mjs, requiredImages) from that registry. The Chronos image build itself downloads
# what the repository's Dockerfile and sandbox/chronos/Dockerfile always do (Chainguard base, PyPI, the
# Alpine/Wolfi package repositories) and nothing else. upgrade-from-ga also pulls the published GA
# Chronos image from <mirror>/gluesync-chronos and asks the backoffice which version that is
# (api.backoffice.molo17.com; SANDBOX_UPGRADE_FROM=<tag> skips the lookup). A missing tool is an error, never an install.
#
#   CI_NODE_INDEX / CI_NODE_TOTAL   the shard (default 1/1: every shard's scenarios, one after the other)
#   SANDBOX_IMAGE_MIRROR            registry/repo holding the sandbox's base images (sandbox/lib/env.mjs)
#   SANDBOX_*_BUDGET                phase budgets in seconds, see below
#   SANDBOX_PATTERNS_CONCURRENCY    cells at a time of dst-fallback-patterns (shard 4), default 6
#   SANDBOX_*                       name and port overrides, see sandbox/lib/env.mjs
#
# The exit status of the scenarios is the sandbox's own (sandbox/lib/gate.mjs): known findings
# (sandbox/known-findings.json) pass, anything else wrong fails.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
MODE=${1:-shard}
SHARD_INDEX=${CI_NODE_INDEX:-1}
SHARD_TOTAL=${CI_NODE_TOTAL:-1}

# What `node run.mjs up --image repo` tags, and where the build job leaves it for the shards.
IMAGE_TAG=chronos-sandbox/chronos:repo
IMAGE_ARCHIVE="$ROOT/sandbox/image/chronos-sandbox-image.tar.gz"

# Real time of each shard (README scenario table) with room to spare. A shard that exceeds its budget
# is killed with a message; the job's own timeout is 60 minutes.
SHARD_1="jobs-crud task-types chains triggers webhooks internal-api settings query-studio ai-agent core-hub-tasks maintenance corehub-faults"
SHARD_1_BUDGET=1500   # ~3 min measured, the rest is room for a slow runner
SHARD_2="corehub-outage corehub-slow restart-catchup upgrade-from-ga"
SHARD_2_BUDGET=1500   # ~10 min (upgrade-from-ga: ~3 min, plus pulling and overlaying the GA image)
SHARD_3="dst-fallback-freeze dst-fallback-customer dst-springforward"
SHARD_3_BUDGET=2100   # ~28 min
SHARD_4="dst-fallback-patterns"
SHARD_4_BUDGET=2700   # ~30-35 min with 6 cells at a time (a frozen Chronos burns one CPU)

PATTERNS_CONCURRENCY=${SANDBOX_PATTERNS_CONCURRENCY:-6}

log() { printf '[sandbox-job] %s\n' "$*"; }
die() { log "$*"; exit 1; }

start_dockerd() {
  if docker info >/dev/null 2>&1; then
    log "docker daemon already running"
    return
  fi
  log "starting dockerd"
  # The image's own entrypoint: cgroup v2 nesting, iptables flavour, docker-init. Naming dockerd
  # as the command keeps it from adding the tcp listener. Data root is the image's /var/lib/docker
  # VOLUME, a real filesystem: overlay2 cannot sit on the container's own overlay.
  dockerd-entrypoint.sh dockerd >/var/log/dockerd.log 2>&1 &
  DOCKERD_PID=$!
  i=0
  until docker info >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 90 ]; then
      log "dockerd did not come up in 90s"
      tail -n 50 /var/log/dockerd.log
      exit 1
    fi
    sleep 1
  done
  driver=$(docker info --format '{{.Driver}}')
  log "dockerd ready (storage driver: $driver)"
  # vfs would copy every layer of every image: hours instead of minutes.
  if [ "$driver" = vfs ]; then
    log "storage driver is vfs: /var/lib/docker is not on a volume, refusing to continue"
    exit 1
  fi
}

# Node 24 is in the CI image; fail loudly rather than fetch anything.
check_tools() {
  command -v node >/dev/null 2>&1 || die "node is missing: run this in chronos-sandbox-ci (ci/sandbox-ci.Dockerfile)"
  case $(node --version) in v24.*) ;; *) die "node $(node --version), the sandbox needs 24: run this in chronos-sandbox-ci" ;; esac
  command -v docker >/dev/null 2>&1 || die "docker is missing: run this in chronos-sandbox-ci"
  log "node $(node --version), $(docker --version)"
}

# phase NAME SECONDS COMMAND...: runs the command under `timeout` and says why it failed. Busybox
# `timeout` sends TERM, then KILL 15 s later.
phase() {
  name=$1; budget=$2; shift 2
  log "$name (budget ${budget}s)"
  rc=0
  timeout -k 15 "$budget" "$@" || rc=$?
  if [ "$rc" -eq 124 ] || [ "$rc" -eq 143 ] || [ "$rc" -eq 137 ]; then
    log "$name HUNG: killed after ${budget}s (exit $rc)"
  elif [ "$rc" -ne 0 ]; then
    log "$name failed (exit $rc)"
  fi
  return "$rc"
}

# Leaves nothing in the daemon behind, whatever the outcome (the daemon dies with the job anyway;
# this is for runs on a long-lived daemon, i.e. a developer's machine). Bounded: a daemon that stopped
# answering must not keep the job `running`. Then the background dockerd is stopped too.
teardown() {
  trap - EXIT
  # A late TERM (the job being cancelled, the daemon going away) must not turn a finished run's exit
  # status into 143 or cut the teardown short.
  trap '' TERM INT HUP
  (cd "$ROOT/sandbox" && timeout -k 10 "${SANDBOX_TEARDOWN_BUDGET:-120}" node run.mjs down --purge) || log "teardown did not finish cleanly (ignored)"
  if [ -n "${DOCKERD_PID:-}" ]; then
    kill "$DOCKERD_PID" 2>/dev/null || true
  fi
}

# --------------------------------------------------------------------------- the build job

build_image() {
  # Building the repository's Dockerfile and the overlay: pip installs, a Rust toolchain for sdists.
  phase "build the Chronos image" "${SANDBOX_BUILD_BUDGET:-1500}" node run.mjs build
  mkdir -p "$(dirname "$IMAGE_ARCHIVE")"
  log "saving $IMAGE_TAG"
  docker save "$IMAGE_TAG" | gzip -1 >"$IMAGE_ARCHIVE"
  log "saved $(du -h "$IMAGE_ARCHIVE" | cut -f1) to ${IMAGE_ARCHIVE#"$ROOT"/}"
}

# --------------------------------------------------------------------------- a shard

run_shard() {
  case "$SHARD_TOTAL:$SHARD_INDEX" in
    4:1) scenarios=$SHARD_1; scen_budget=$SHARD_1_BUDGET ;;
    4:2) scenarios=$SHARD_2; scen_budget=$SHARD_2_BUDGET ;;
    4:3) scenarios=$SHARD_3; scen_budget=$SHARD_3_BUDGET ;;
    4:4) scenarios=$SHARD_4; scen_budget=$SHARD_4_BUDGET ;;
    1:1) scenarios="$SHARD_1 $SHARD_2 $SHARD_3 $SHARD_4"; scen_budget=$((SHARD_1_BUDGET + SHARD_2_BUDGET + SHARD_3_BUDGET + SHARD_4_BUDGET)) ;;
    *) die "there are 4 shards (or 1 for everything), not $SHARD_INDEX/$SHARD_TOTAL: edit the lists in ci/sandbox-job.sh" ;;
  esac
  # (`phase` has its own `budget`: sh has no local variables, so this one is named differently.)
  scen_budget=${SANDBOX_SCENARIOS_BUDGET:-$scen_budget}

  up_budget=${SANDBOX_UP_BUDGET:-600}
  if [ -f "$IMAGE_ARCHIVE" ]; then
    log "loading the Chronos image built by sandbox_image"
    gunzip -c "$IMAGE_ARCHIVE" | docker load
  else
    # No artifact (run by hand): `up` builds the image itself.
    log "no ${IMAGE_ARCHIVE#"$ROOT"/}: building the Chronos image here"
    up_budget=${SANDBOX_UP_BUDGET:-1800}
  fi

  phase "up" "$up_budget" node run.mjs up

  extra=
  case " $scenarios " in *" dst-fallback-patterns "*) extra="--concurrency $PATTERNS_CONCURRENCY" ;; esac
  log "shard $SHARD_INDEX/$SHARD_TOTAL: $scenarios"
  # shellcheck disable=SC2086  # the lists and flags are meant to split
  phase "scenarios" "$scen_budget" node run.mjs scenario $scenarios $extra
}

check_tools
start_dockerd
cd "$ROOT/sandbox"
trap teardown EXIT
# A cancelled job gets TERM: run the teardown through EXIT.
trap 'exit 143' TERM INT

case "$MODE" in
  build) build_image ;;
  shard) run_shard ;;
  *) die "usage: sh ci/sandbox-job.sh [build]" ;;
esac
