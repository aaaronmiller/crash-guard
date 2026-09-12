#!/usr/bin/env bash
# Install crash-guard's scheduled jobs.
#
# Idempotent: entries are fenced by markers and replaced wholesale on re-run,
# so running this twice does not give you two of everything. Removing the
# fence removes the jobs.
#
# Reads times from ~/.config/crash-guard/config.yaml when present, otherwise
# uses the defaults below. No yaml parser -- these are three scalars and
# depending on PyYAML for them would be a dependency per scheduled minute.

set -uo pipefail

BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../bin" && pwd)"
CFG="${CRASH_GUARD_CONFIG:-$HOME/.config/crash-guard/config.yaml}"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/crash-guard"
MARK_BEGIN="# >>> crash-guard scheduled jobs >>>"
MARK_END="# <<< crash-guard scheduled jobs <<<"

mkdir -p "$STATE"

cfg_get() {  # cfg_get <key> <default>
    local v=""
    [ -r "$CFG" ] && v=$(grep -E "^\s*$1:" "$CFG" 2>/dev/null | head -1 |
        sed -E 's/^[^:]*:[[:space:]]*"?([^"#]*)"?.*/\1/' |
        sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')
    printf '%s' "${v:-$2}"
}

DAILY=$(cfg_get daily_collect "07:30")
WEEKLY=$(cfg_get weekly_report "Mon 08:00")
CASS=$(cfg_get cass_reindex "04:40")

dh() { printf '%s' "${1#*:}"; }   # minutes
hh() { printf '%s' "${1%%:*}"; }  # hours

DAILY_H=$(hh "$DAILY"); DAILY_M=$(dh "$DAILY")
CASS_H=$(hh "$CASS");   CASS_M=$(dh "$CASS")
WEEK_TIME=${WEEKLY##* }; WEEK_DAY=${WEEKLY%% *}
WEEK_H=$(hh "$WEEK_TIME"); WEEK_M=$(dh "$WEEK_TIME")
case "${WEEK_DAY,,}" in
    mon) WD=1 ;; tue) WD=2 ;; wed) WD=3 ;; thu) WD=4 ;;
    fri) WD=5 ;; sat) WD=6 ;; sun) WD=0 ;; *) WD=1 ;;
esac

# cass indexing is memory-bound, not CPU-bound: it refuses to schedule workers
# unless the host has ~4 GiB free, and on a memory-capped box it will park and
# then be killed by its own stall detector. RUST_MIN_STACK works around a real
# stack-overflow bug and is needed regardless of how much memory you have.
CASS_ENV="RUST_MIN_STACK=134217728 CASS_INDEX_STALL_DETECT_SECS=300 CASS_INDEX_STALL_ABORT_SECS=0"

BLOCK=$(cat <<EOF
$MARK_BEGIN
# Managed by crash-guard/scripts/install-cron.sh -- edits inside this fence
# are overwritten on re-run. Change ~/.config/crash-guard/config.yaml instead.
$DAILY_M $DAILY_H * * *   $BIN_DIR/cg-telemetry collect >>$STATE/telemetry.log 2>&1
$WEEK_M $WEEK_H * * $WD   $BIN_DIR/cg-telemetry report  >>$STATE/telemetry.log 2>&1
$CASS_M $CASS_H * * *     $CASS_ENV cass index          >>$STATE/cass-reindex.log 2>&1
$MARK_END
EOF
)

existing=$(crontab -l 2>/dev/null || true)
cleaned=$(printf '%s\n' "$existing" | sed "/$MARK_BEGIN/,/$MARK_END/d")
printf '%s\n%s\n' "$cleaned" "$BLOCK" | sed '/^$/N;/^\n$/D' | crontab -

echo "installed:"
echo "  daily collect   $DAILY"
echo "  weekly report   $WEEK_DAY $WEEK_TIME"
echo "  cass reindex    $CASS   (memory-bound; move or disable if another machine rebuilds)"
echo
echo "verify with:  crontab -l | sed -n '/crash-guard scheduled/,/<<</p'"
echo "remove with:  crontab -l | sed '/>>> crash-guard/,/<<< crash-guard/d' | crontab -"
