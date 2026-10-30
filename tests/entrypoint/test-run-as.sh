#!/bin/sh
# Tests the run-as decision of entrypoint.sh (root or uid/gid 1017) without a container.
#
# entrypoint.sh runs against a temporary tree standing in for /app (CHRONOS_APP_DIR,
# DATA_DIR, LOG_DIR, ...). id, su-exec/gosu, chown, python3 and the app command are
# stubs on PATH that record their calls in $WORK/calls.log, so the script checks
# what the entrypoint decides and which commands it would run as whom. The real
# container behaviour is covered by the Docker checks in the merge request.
#
# Usage: sh tests/entrypoint/test-run-as.sh        (POSIX sh; also run with dash)

HERE=$(cd "$(dirname "$0")" && pwd)
ENTRYPOINT="$HERE/../../entrypoint.sh"
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT

PASS=0
FAIL=0
CASE=""

# ---- helpers ---------------------------------------------------------------

# new_case NAME [su-exec|gosu|none]: fresh tree + stubs
new_case() {
    CASE="$1"
    WORK="$ROOT/$(echo "$1" | tr -c 'A-Za-z0-9\n' '_')"
    APP="$WORK/app"
    DATA="$APP/data"
    LOGS="$APP/logs"
    BIN="$WORK/bin"
    CALLS="$WORK/calls.log"
    OUT="$WORK/out.log"
    mkdir -p "$DATA" "$LOGS" "$APP/migrations" "$BIN" "$WORK/shared" "$WORK/legacy"
    : > "$CALLS"
    printf '#!/bin/sh\nexit 0\n' > "$APP/migrations/run_migrations.sh"

    cat > "$BIN/id" <<'STUB'
#!/bin/sh
if [ "$1" = "-u" ]; then echo "${STUB_UID:-0}"; exit 0; fi
exec /usr/bin/id "$@"
STUB
    cat > "$BIN/chown" <<'STUB'
#!/bin/sh
echo "chown $*" >> "$STUB_CALLS"
[ "${STUB_CHOWN_FAIL:-0}" = "1" ] && exit 1
exit 0
STUB
    # touch fails for the directory named in STUB_UNWRITABLE (write probe of a non-root start)
    cat > "$BIN/touch" <<'STUB'
#!/bin/sh
[ -n "${STUB_UNWRITABLE:-}" ] && [ "$(dirname "$1")" = "$STUB_UNWRITABLE" ] && exit 1
exec /usr/bin/touch "$@"
STUB
    cat > "$BIN/python3" <<'STUB'
#!/bin/sh
echo "python3 $1" >> "$STUB_CALLS"
exit 0
STUB
    cat > "$BIN/app" <<'STUB'
#!/bin/sh
echo "app $*" >> "$STUB_CALLS"
exit 0
STUB
    # Stand-in for su-exec/gosu: logs the call; the writability probe (sh -c) and the
    # readability probe (test -r) answer from STUB_WRITABLE / STUB_UNREADABLE
    cat > "$BIN/runas-stub" <<'STUB'
#!/bin/sh
tool=$(basename "$0")
spec="$1"; shift
echo "$tool $spec $*" >> "$STUB_CALLS"
case "$1" in
    sh)
        if [ "$2" = "-c" ]; then [ "${STUB_WRITABLE:-1}" = "1" ]; exit $?; fi
        ;;
    test)
        [ "$3" = "${STUB_UNREADABLE:-}" ] && exit 1
        exit 0
        ;;
esac
exec "$@"
STUB
    chmod +x "$BIN"/*
    case "${2:-su-exec}" in
        su-exec) ln -s runas-stub "$BIN/su-exec" ;;
        gosu) ln -s runas-stub "$BIN/gosu" ;;
        none) ;;
    esac
}

# run_entrypoint [VAR=value ...]: runs entrypoint.sh with "app one two" as the command
run_entrypoint() {
    (
        unset CHRONOS_RUN_AS CRON_LOG_DIR GLUESYNC_LICENSE_FILE GLUESYNC_SECURITY_CONFIG \
              SSL_CERT_FILE SSL_KEY_FILE SSL_P12_PATH STUB_UID STUB_CHOWN_FAIL STUB_WRITABLE \
              STUB_UNREADABLE STUB_UNWRITABLE
        export CHRONOS_APP_DIR="$APP" DATA_DIR="$DATA" LOG_DIR="$LOGS" \
               CHRONOS_SHARED_DIR="$WORK/shared" CHRONOS_LEGACY_DIR="$WORK/legacy" \
               TIMEZONE=UTC DB_URL="sqlite:///$DATA/scheduler.db" STUB_CALLS="$CALLS"
        for kv in "$@"; do export "${kv?}"; done
        PATH="$BIN:/usr/bin:/bin" exec sh "$ENTRYPOINT" app one two
    ) > "$OUT" 2>&1
    RC=$?
}

ok() { PASS=$((PASS + 1)); }
bad() { FAIL=$((FAIL + 1)); echo "FAIL [$CASE] $1"; echo "  --- output"; sed 's/^/  /' "$OUT"; echo "  --- calls"; sed 's/^/  /' "$CALLS"; }

has() { # file, fixed string, message
    if grep -qF -- "$2" "$1"; then ok; else bad "${3:-missing: $2}"; fi
}
hasnt() {
    if grep -qF -- "$2" "$1"; then bad "${3:-unexpected: $2}"; else ok; fi
}
marker_is() {
    if [ "$(cat "$DATA/.chronos-run-as" 2>/dev/null)" = "$1" ]; then ok; else bad "marker is '$(cat "$DATA/.chronos-run-as" 2>/dev/null)', expected '$1'"; fi
}
no_marker() {
    if [ ! -e "$DATA/.chronos-run-as" ]; then ok; else bad "unexpected marker"; fi
}
started() { # the app command ran
    if [ "$RC" = "0" ]; then ok; else bad "entrypoint exited $RC"; fi
    has "$CALLS" "app one two" "the application command did not run"
}
runs_as_root() {
    started
    has "$OUT" "run-as=root"
    hasnt "$CALLS" "su-exec"
    hasnt "$CALLS" "gosu"
    hasnt "$CALLS" "chown"
}
runs_as_1017() { # tool
    started
    has "$OUT" "run-as=gluesync"
    has "$CALLS" "$1 1017:1017 app one two" "app not started through $1"
    has "$CALLS" "$1 1017:1017 python3" "database init not run as 1017"
    has "$CALLS" "$1 1017:1017 sh $APP/migrations/run_migrations.sh" "migrations not run as 1017"
}

# ---- 1. not started as root -------------------------------------------------
new_case "non-root start"
run_entrypoint STUB_UID=1017 CHRONOS_RUN_AS=gluesync
started
has "$OUT" "started as uid 1017"
has "$OUT" "CHRONOS_RUN_AS=gluesync ignored"
hasnt "$CALLS" "chown"
hasnt "$CALLS" "su-exec"
no_marker

new_case "non-root start, writable dirs -> no error, no probe file left behind"
run_entrypoint STUB_UID=1017
started
hasnt "$OUT" "not writable"
if ls -A "$DATA" "$LOGS" | grep -q chronos-write-test; then bad "probe file left behind"; else ok; fi

new_case "non-root start, unwritable data dir -> error names dir, owner and uid, app still starts"
run_entrypoint STUB_UID=1017 STUB_UNWRITABLE="$DATA"
started
has "$OUT" "ERROR: $DATA is not writable by uid 1017 (owned by uid "
has "$OUT" "podman unshare chown -R 0:0 <host dir>"
hasnt "$OUT" "$LOGS is not writable"
hasnt "$CALLS" "chown"
no_marker

new_case "non-root start, unwritable LOG_DIR -> reported, data dir not"
mkdir -p "$WORK/cron"
run_entrypoint STUB_UID=1017 CRON_LOG_DIR="$WORK/cron" STUB_UNWRITABLE="$LOGS"
started
has "$OUT" "ERROR: $LOGS is not writable by uid 1017"
hasnt "$OUT" "$DATA is not writable"
new_case "non-root start, unwritable CRON_LOG_DIR"
mkdir -p "$WORK/cron"
run_entrypoint STUB_UID=1017 CRON_LOG_DIR="$WORK/cron" STUB_UNWRITABLE="$WORK/cron"
started
has "$OUT" "ERROR: $WORK/cron is not writable by uid 1017"

# ---- 2. explicit root ---------------------------------------------------------
new_case "explicit root on empty data"
run_entrypoint CHRONOS_RUN_AS=root
runs_as_root
has "$OUT" "CHRONOS_RUN_AS=root"
marker_is root

new_case "explicit root overrides a gluesync marker"
echo gluesync > "$DATA/.chronos-run-as"
run_entrypoint CHRONOS_RUN_AS=root
runs_as_root
marker_is root

# ---- 3. explicit gluesync -----------------------------------------------------
new_case "explicit gluesync migrates a root install"
echo db > "$DATA/scheduler.db"
echo root > "$DATA/.chronos-run-as"
run_entrypoint CHRONOS_RUN_AS=gluesync
runs_as_1017 su-exec
has "$CALLS" "chown -R 1017:1017 $DATA" "data not chowned recursively"
has "$CALLS" "chown -R 1017:1017 $LOGS" "logs not chowned recursively"
marker_is gluesync

new_case "CRON_LOG_DIR outside LOG_DIR is chowned too"
mkdir -p "$WORK/cron"
run_entrypoint CHRONOS_RUN_AS=gluesync CRON_LOG_DIR="$WORK/cron"
runs_as_1017 su-exec
has "$CALLS" "chown -R 1017:1017 $WORK/cron"

new_case "invalid CHRONOS_RUN_AS is ignored (existing data -> root)"
echo db > "$DATA/scheduler.db"
run_entrypoint CHRONOS_RUN_AS=admin
runs_as_root
has "$OUT" "is not root or gluesync"
marker_is root

# ---- 4. marker ----------------------------------------------------------------
new_case "marker root keeps root on an otherwise empty directory"
echo root > "$DATA/.chronos-run-as"
run_entrypoint
runs_as_root
has "$OUT" "marker"
marker_is root

new_case "marker gluesync re-chowns only what is not 1017's"
echo db > "$DATA/scheduler.db"
echo gluesync > "$DATA/.chronos-run-as"
run_entrypoint
runs_as_1017 su-exec
hasnt "$CALLS" "chown -R"
has "$CALLS" "chown 1017:1017" "incremental chown not run"
marker_is gluesync

# ---- 5. existing install (no marker) -------------------------------------------
new_case "existing data without marker -> root and marker root"
echo db > "$DATA/scheduler.db"
run_entrypoint
runs_as_root
has "$OUT" "existing install"
marker_is root

new_case "hidden file counts as existing data"
echo x > "$DATA/.hidden"
run_entrypoint
runs_as_root
marker_is root

new_case "restart of an existing install stays root"
echo db > "$DATA/scheduler.db"
run_entrypoint
: > "$CALLS"
run_entrypoint
runs_as_root
has "$OUT" "marker"
marker_is root

# ---- 6. new install --------------------------------------------------------------
new_case "empty data -> gluesync, chown, marker"
run_entrypoint
runs_as_1017 su-exec
has "$OUT" "new install"
has "$CALLS" "chown -R 1017:1017 $DATA"
has "$CALLS" "chown -R 1017:1017 $LOGS"
marker_is gluesync

new_case "lost+found alone is still a new install"
mkdir "$DATA/lost+found"
run_entrypoint
runs_as_1017 su-exec
marker_is gluesync

new_case "restart of a new install stays gluesync"
run_entrypoint
echo db > "$DATA/scheduler.db"
: > "$CALLS"
run_entrypoint
runs_as_1017 su-exec
has "$OUT" "marker"
hasnt "$CALLS" "chown -R"

new_case "gosu is used when su-exec is absent (Debian)" gosu
run_entrypoint
runs_as_1017 gosu

# ---- 7. failures never stop the start ---------------------------------------------
new_case "chown fails and 1017 cannot write -> root fallback, marker keeps gluesync"
run_entrypoint STUB_CHOWN_FAIL=1 STUB_WRITABLE=0
started
has "$OUT" "running as root for this start"
has "$OUT" "run-as=root"
hasnt "$CALLS" "su-exec 1017:1017 app"
marker_is gluesync

new_case "chown reports errors but dirs are writable -> still drops"
run_entrypoint STUB_CHOWN_FAIL=1 STUB_WRITABLE=1
runs_as_1017 su-exec
has "$OUT" "chown of the runtime directories reported errors"

new_case "marker cannot be written (read-only volume) -> start continues"
mkdir "$DATA/.chronos-run-as"   # a directory: the write fails even as root
run_entrypoint
runs_as_1017 su-exec
has "$OUT" "cannot write $DATA/.chronos-run-as"

new_case "marker cannot be written on an existing install -> start continues as root"
echo db > "$DATA/scheduler.db"
mkdir "$DATA/.chronos-run-as"
run_entrypoint
started
has "$OUT" "run-as=root"
has "$OUT" "cannot write $DATA/.chronos-run-as"
hasnt "$CALLS" "su-exec"

new_case "no su-exec and no gosu -> root with a warning" none
run_entrypoint
started
has "$OUT" "neither su-exec nor gosu"
has "$OUT" "run-as=root"
hasnt "$CALLS" "chown"

new_case "garbage marker is decided again"
echo junk > "$DATA/.chronos-run-as"
run_entrypoint
runs_as_1017 su-exec
has "$OUT" "expected root or gluesync"
marker_is gluesync

# ---- 8. shared files uid 1017 cannot read ---------------------------------------------
new_case "new install, unreadable key -> root this start, gluesync marker, next start drops"
echo '{"ssl":{"sslCertificatePath":"'"$WORK"'/shared/cert.p12"}}' > "$WORK/shared/security-config.json"
echo p12 > "$WORK/shared/cert.p12"
run_entrypoint STUB_UNREADABLE="$WORK/shared/cert.p12"
started
has "$OUT" "cannot read $WORK/shared/cert.p12"
has "$OUT" "run-as=root"
hasnt "$CALLS" "su-exec 1017:1017 app"
marker_is gluesync
: > "$CALLS"
run_entrypoint
runs_as_1017 su-exec

new_case "unreadable license in auto mode (marker gluesync) -> root fallback"
echo gluesync > "$DATA/.chronos-run-as"
echo lic > "$WORK/shared/gs-license.dat"
run_entrypoint STUB_UNREADABLE="$WORK/shared/gs-license.dat"
started
has "$OUT" "cannot read $WORK/shared/gs-license.dat"
hasnt "$CALLS" "su-exec 1017:1017 app"
marker_is gluesync

new_case "unreadable file named by GLUESYNC_LICENSE_FILE"
echo lic > "$WORK/custom.dat"
run_entrypoint GLUESYNC_LICENSE_FILE="$WORK/custom.dat" STUB_UNREADABLE="$WORK/custom.dat"
started
has "$OUT" "cannot read $WORK/custom.dat"
hasnt "$CALLS" "su-exec 1017:1017 app"

new_case "explicit gluesync with an unreadable file still drops, with a warning"
echo lic > "$WORK/shared/gs-license.dat"
run_entrypoint CHRONOS_RUN_AS=gluesync STUB_UNREADABLE="$WORK/shared/gs-license.dat"
runs_as_1017 su-exec
has "$OUT" "cannot read $WORK/shared/gs-license.dat"

new_case "readable shared files do not matter"
echo lic > "$WORK/shared/gs-license.dat"
run_entrypoint
runs_as_1017 su-exec

echo
echo "run-as tests: $PASS checks passed, $FAIL failed"
[ "$FAIL" = "0" ]
