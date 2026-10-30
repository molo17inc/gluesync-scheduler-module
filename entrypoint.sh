#!/bin/sh
set -e

# Paths. The defaults are the image layout; the environment overrides exist so
# tests/entrypoint/test-run-as.sh can run this script against a temporary tree.
APP_DIR="${CHRONOS_APP_DIR:-/app}"
DATA_PATH="${DATA_DIR:-$APP_DIR/data}"
LOG_PATH="${LOG_DIR:-$APP_DIR/logs}"
CRON_LOG_PATH="${CRON_LOG_DIR:-$LOG_PATH}"
SHARED_DIR="${CHRONOS_SHARED_DIR:-/opt/gluesync/shared}"
LEGACY_DIR="${CHRONOS_LEGACY_DIR:-/opt/gluesync/data}"

# Run-as user of new installs: uid/gid 1017, the gluesync user of the Core Hub image
RUN_UID=1017
RUN_GID=1017
RUN_SPEC="$RUN_UID:$RUN_GID"
MARKER="$DATA_PATH/.chronos-run-as"

say() { echo "chronos: $*"; }
warn() { echo "chronos: WARNING: $*" >&2; }
err() { echo "chronos: ERROR: $*" >&2; }

# Only auto-detect timezone if TIMEZONE isn't already set
if [ -z "$TIMEZONE" ]; then
    echo "TIMEZONE not set, auto-detecting..."
    # Linux: Try /etc/timezone
    if [ -f /etc/timezone ]; then
        export TIMEZONE=$(cat /etc/timezone | tr -d '\n')
    # Linux: Try /etc/localtime symlink
    elif [ -L /etc/localtime ]; then
        tz_path=$(readlink /etc/localtime)
        case "$tz_path" in
            *zoneinfo/*)
                export TIMEZONE="${tz_path##*/zoneinfo/}"
                ;;
            *)
                export TIMEZONE="UTC"
                ;;
        esac
    # Windows: Use TZ env if set (Docker for Windows sets this if --env TZ=...)
    elif [ ! -z "$TZ" ]; then
        export TIMEZONE="$TZ"
    else
        export TIMEZONE="UTC"
    fi
else
    echo "Using provided TIMEZONE: $TIMEZONE"
fi

echo "Detected TIMEZONE: $TIMEZONE"

# ---------------------------------------------------------------------------
# Who runs the app
#
# The image starts as root. New installs run Chronos as uid/gid 1017; existing
# installs keep running as root, because their data directory (bind mount) and
# logs are root-owned and a privilege drop would lock the app out of them.
# Everything the app writes lives in the data and log directories, and /tmp:
#   - DATA_DIR: scheduler.db and its -wal/-shm/-journal files
#   - LOG_DIR / CRON_LOG_DIR: chronos.log, play_pause.log, sdk_client.log,
#     job_runner.log, job_<id>.log, job_<id>.error.log
#   - /tmp: the PEM files extracted from a PKCS12 certificate (run_scheduler.py, SDK)
# Nothing is written under /app or /opt/gluesync, and the shared files (license,
# security config, keystore) are only read.
#
# Decision order:
#   1. not started as root (Podman rootless keep-id, Kubernetes runAsUser):
#      run as the current user, touch nothing
#   2. CHRONOS_RUN_AS=root|gluesync
#   3. marker file $DATA_DIR/.chronos-run-as (root|gluesync) written by an earlier start
#   4. no marker: data directory not empty -> existing install -> root;
#      empty -> new install -> gluesync
# The decision is stored in the marker, so it does not change on restart.
# Nothing here ever stops the start: a failed chown or an unwritable marker is a
# warning, and a drop that could not write the data directory falls back to root.
# ---------------------------------------------------------------------------

# su-exec (Wolfi) or gosu (Debian): both are called as "<tool> user:group command..."
SU_EXEC=""
for candidate in su-exec gosu; do
    if command -v "$candidate" >/dev/null 2>&1; then
        SU_EXEC="$candidate"
        break
    fi
done

# True when the data directory holds anything besides lost+found and the marker
data_dir_has_content() {
    for entry in "$DATA_PATH"/* "$DATA_PATH"/.[!.]* "$DATA_PATH"/..?*; do
        if [ -e "$entry" ] || [ -L "$entry" ]; then
            case "${entry##*/}" in
                lost+found|.chronos-run-as) ;;
                *) return 0 ;;
            esac
        fi
    done
    return 1
}

# Runtime directories, de-duplicated (CRON_LOG_DIR defaults to LOG_DIR)
runtime_dirs() {
    echo "$DATA_PATH"
    echo "$LOG_PATH"
    if [ "$CRON_LOG_PATH" != "$LOG_PATH" ] && [ "$CRON_LOG_PATH" != "$DATA_PATH" ]; then
        echo "$CRON_LOG_PATH"
    fi
}

# Hand the runtime directories to uid/gid 1017.
#   full:        chown -R everything (new install, explicit CHRONOS_RUN_AS=gluesync)
#   incremental: only entries not owned by 1017 yet (restarts), so the cost stays
#                small and files dropped in by root later still work
# Returns non-zero when a chown failed; the caller decides what that means.
hand_over_runtime_dirs() {
    mode="$1"
    status=0
    for dir in $(runtime_dirs); do
        mkdir -p "$dir" 2>/dev/null || true
        if [ "$mode" = "full" ]; then
            chown -R "$RUN_SPEC" "$dir" || status=1
        else
            find "$dir" ! -user "$RUN_UID" -exec chown "$RUN_SPEC" {} + || status=1
        fi
    done
    return $status
}

# Succeeds when uid 1017 can write every runtime directory
runtime_dirs_writable_by_run_user() {
    for dir in $(runtime_dirs); do
        "$SU_EXEC" "$RUN_SPEC" sh -c 'test -w "$1"' sh "$dir" || return 1
    done
    return 0
}

# The shared files the app reads: license, security config and the certificate the
# security config points to (plus the explicit SSL_* variables). A file that exists
# but that uid 1017 cannot read (for instance a root-owned 0600 key written by an
# installer before Core Hub's first chown) would crash the app after the drop.
# Prints the first unreadable one and fails when there is one.
shared_file_unreadable_by_run_user() {
    list=""
    for pair in "GLUESYNC_LICENSE_FILE:gs-license.dat" "GLUESYNC_SECURITY_CONFIG:security-config.json"; do
        var="${pair%%:*}"
        name="${pair#*:}"
        eval "value=\${$var:-}"
        for path in "$value" "$SHARED_DIR/$name" "$LEGACY_DIR/$name"; do
            if [ -n "$path" ] && [ -e "$path" ]; then
                list="$list
$path"
                if [ "$name" = "security-config.json" ]; then
                    cert=$(sed -n 's/.*"sslCertificatePath"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$path" 2>/dev/null | head -n 1)
                    case "$cert" in
                        ""|/*) ;;
                        *) cert="$(dirname "$path")/$cert" ;; # relative: next to the config, as the SDK resolves it
                    esac
                    if [ -n "$cert" ] && [ -e "$cert" ]; then
                        list="$list
$cert"
                    fi
                fi
                break
            fi
        done
    done
    for path in "${SSL_CERT_FILE:-}" "${SSL_KEY_FILE:-}" "${SSL_P12_PATH:-}"; do
        if [ -n "$path" ] && [ -e "$path" ]; then
            list="$list
$path"
        fi
    done
    old_ifs="$IFS"
    IFS='
'
    for path in $list; do
        [ -n "$path" ] || continue
        if ! "$SU_EXEC" "$RUN_SPEC" test -r "$path"; then
            IFS="$old_ifs"
            echo "$path"
            return 0
        fi
    done
    IFS="$old_ifs"
    return 1
}

# Started without root: nothing can be chowned, so only report a runtime directory the
# current user cannot write (typically files written by container root of an older
# image, now owned by a subuid on rootless Podman). The app still starts, so its own
# error and restart behaviour stays as it was; the message says what to do.
# Writability is probed with a real file, which also catches read-only mounts.
report_unwritable_runtime_dirs() {
    for dir in $(runtime_dirs); do
        [ -d "$dir" ] || continue
        probe="$dir/.chronos-write-test"
        if touch "$probe" 2>/dev/null; then
            rm -f "$probe" 2>/dev/null || true
            continue
        fi
        set -- $(ls -ldn "$dir" 2>/dev/null)
        owner="${3:-unknown}"
        err "$dir is not writable by uid $(id -u) (owned by uid $owner); Chronos will fail to write there."
        err "Fix: give uid $(id -u) ownership of the host directory mounted at $dir. On rootless Podman run, as the user that owns the volume: podman unshare chown -R 0:0 <host dir> (Gluesync Control does this automatically)."
    done
    return 0
}

write_marker() {
    if [ -f "$MARKER" ] && [ "$(head -n 1 "$MARKER" 2>/dev/null | tr -d '[:space:]')" = "$1" ]; then
        return 0
    fi
    if ! { printf '%s\n' "$1" > "$MARKER"; } 2>/dev/null; then
        warn "cannot write $MARKER (read-only volume?); continuing with run-as=$1"
        return 0
    fi
    if [ "$1" = "gluesync" ]; then
        chown "$RUN_SPEC" "$MARKER" 2>/dev/null || true
    fi
}

# Decide: sets RUN_AS (root|gluesync), REASON, and whether the choice is automatic
RUN_AS=root
REASON=""
AUTO=0
RUNNER=""

if [ "$(id -u)" != "0" ]; then
    say "started as uid $(id -u) (not root): running as the current user, ownership left alone"
    if [ -n "${CHRONOS_RUN_AS:-}" ]; then
        say "CHRONOS_RUN_AS=$CHRONOS_RUN_AS ignored: only a root start can change user"
    fi
    report_unwritable_runtime_dirs
else
    mkdir -p "$DATA_PATH" "$LOG_PATH" 2>/dev/null || true
    case "${CHRONOS_RUN_AS:-}" in
        root|gluesync)
            RUN_AS="$CHRONOS_RUN_AS"
            REASON="CHRONOS_RUN_AS=$CHRONOS_RUN_AS"
            ;;
        "") ;;
        *)
            warn "CHRONOS_RUN_AS='$CHRONOS_RUN_AS' is not root or gluesync; ignoring it"
            ;;
    esac

    FULL_CHOWN=1
    if [ -z "$REASON" ]; then
        AUTO=1
        marker_value=""
        if [ -f "$MARKER" ]; then
            marker_value=$(head -n 1 "$MARKER" 2>/dev/null | tr -d '[:space:]') || marker_value=""
        fi
        case "$marker_value" in
            root|gluesync)
                RUN_AS="$marker_value"
                REASON="marker $MARKER"
                FULL_CHOWN=0
                ;;
            *)
                if [ -n "$marker_value" ]; then
                    warn "$MARKER holds '$marker_value' (expected root or gluesync); deciding again"
                fi
                if data_dir_has_content; then
                    RUN_AS=root
                    REASON="existing install: $DATA_PATH is not empty"
                else
                    RUN_AS=gluesync
                    REASON="new install: $DATA_PATH is empty"
                fi
                ;;
        esac
    fi

    if [ "$RUN_AS" = "gluesync" ] && [ -z "$SU_EXEC" ]; then
        warn "neither su-exec nor gosu is installed; cannot drop to uid $RUN_UID, running as root"
        RUN_AS=root
    fi

    if [ "$RUN_AS" = "gluesync" ]; then
        # The decision (marker) stands; the checks below only decide this start
        if [ "$FULL_CHOWN" = "1" ]; then
            hand_over_runtime_dirs full || warn "chown of the runtime directories reported errors"
        else
            hand_over_runtime_dirs incremental || warn "chown of the runtime directories reported errors"
        fi
        write_marker gluesync
        unreadable=$(shared_file_unreadable_by_run_user) || unreadable=""
        if ! runtime_dirs_writable_by_run_user; then
            warn "uid $RUN_UID cannot write $DATA_PATH / $LOG_PATH (chown not possible on this volume?): running as root for this start"
            RUN_AS=root
            REASON="$REASON, fallback after unwritable runtime directories"
        elif [ -n "$unreadable" ] && [ "$AUTO" = "1" ]; then
            warn "uid $RUN_UID cannot read $unreadable: running as root for this start; the next start drops to uid $RUN_UID once the file is readable (Core Hub chowns shared files to $RUN_UID)"
            RUN_AS=root
            REASON="$REASON, fallback after unreadable shared file"
        elif [ -n "$unreadable" ]; then
            warn "uid $RUN_UID cannot read $unreadable (CHRONOS_RUN_AS=gluesync is explicit, so the app still runs as $RUN_UID and may not start)"
        fi
    else
        write_marker root
    fi

    if [ "$RUN_AS" = "gluesync" ]; then
        RUNNER="$SU_EXEC $RUN_SPEC"
    fi
    say "run-as=$RUN_AS ($REASON)"
fi

# Make sure run_job.sh and run_scheduler.py are executable
if [ -f "$APP_DIR/run_job.sh" ]; then
    echo "Making run_job.sh executable"
    chmod +x "$APP_DIR/run_job.sh" || true
fi

if [ -f "$APP_DIR/run_scheduler.py" ]; then
    echo "Making run_scheduler.py executable"
    chmod +x "$APP_DIR/run_scheduler.py" || true
fi

# Package is preinstalled from a wheel during image build; skip runtime installation
echo "Package already installed from prebuilt wheel; skipping installation."

# From here on everything runs as the chosen user (RUNNER is empty for root and for
# a start that was not root), so the database files are created with the right owner.

# Initialize the database if it doesn't exist
echo "Checking database initialization..."
DATA_DIR=/app/data DB_URL=sqlite:////${DATA_DIR}/scheduler.db $RUNNER python3 -c "from gluesync_scheduler.db.database import engine; from gluesync_scheduler.models.models import Base; Base.metadata.create_all(bind=engine)" || {
    echo "Error initializing database schema"
    exit 1
}
echo "Database schema initialized successfully"

# Run database migrations to ensure schema is up-to-date
echo "Running database migrations..."
# Forward DB URL explicitly for SQLAlchemy-based migrations; group_ids migration uses file path internally
$RUNNER sh "$APP_DIR/migrations/run_migrations.sh" --db-url "${DB_URL}" || {
    echo "Error running database migrations"
    exit 1
}
echo "Database migrations completed"

# Execute the CMD command
echo "Starting application..."
exec $RUNNER "$@"
