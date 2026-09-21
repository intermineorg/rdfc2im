#!/usr/bin/env bash
# Live progress dashboard for tools/full-build.sh.
#
# Reads only the files full-build.sh already writes (LOG_DIR/build-<id>.{log,status,timing.tsv})
# and the canonical phase order via `full-build.sh --list-phases` - no changes to that script, so
# this can't drift from what it actually runs and can't affect the build itself either.
#
# Usage:
#   tools/watch-build.sh [--run-id ID] [--once] [--interval N]
#
#   --run-id ID   Watch a specific run (default: the most recently started one under LOG_DIR).
#   --once        Render one frame and exit, instead of looping (for logging/CI use).
#   --interval N  Seconds between refreshes (default 3).
#
# Logs default to .build-logs/ in this repository, which full-build.sh also defaults to - so this
# works unchanged from a host terminal watching a build running in a sandbox that shares the
# checkout. If the build used a different LOG_DIR, set the same one here.
set -euo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)

LOG_DIR=${LOG_DIR:-"$HERE/.build-logs"}    # same default as full-build.sh

RUN_ID=""
ONCE=0
INTERVAL=3

while [ $# -gt 0 ]; do
  case "$1" in
    --run-id) RUN_ID=$2; shift ;;
    --once) ONCE=1 ;;
    --interval) INTERVAL=$2; shift ;;
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ -z "$RUN_ID" ]; then
  latest=$(ls -t "$LOG_DIR"/build-*.status 2>/dev/null | head -1 || true)
  [ -n "$latest" ] || { echo "no build-*.status file under $LOG_DIR - is full-build.sh running (with a matching LOG_DIR)?" >&2; exit 1; }
  RUN_ID=$(basename "$latest" .status); RUN_ID=${RUN_ID#build-}
fi

STATUS_FILE="$LOG_DIR/build-$RUN_ID.status"
LOGFILE="$LOG_DIR/build-$RUN_ID.log"
TIMING_TSV="$LOG_DIR/build-$RUN_ID.timing.tsv"

[ -f "$LOGFILE" ] || { echo "no logfile at $LOGFILE for run $RUN_ID" >&2; exit 1; }

# Canonical phase order straight from full-build.sh's own PHASES=(...) literal, not duplicated
# here - a phase added/renamed/reordered there shows up correctly with no edit needed in this
# file. Parsed as plain text (sed/parameter-expansion/word-splitting) rather than executed:
# full-build.sh needs bash 4+ (associative arrays) and is meant to run only inside the build
# sandbox, but this dashboard is meant to also run standalone against a mirrored log directory
# from a plain macOS Terminal, whose stock bash is 3.2 (no `declare -A`, no `mapfile`) - so this
# script must never invoke full-build.sh itself, only read it.
PHASES=$(sed -n '/^PHASES=(/,/)/p' "$HERE/tools/full-build.sh" | tr '\n' ' ')
PHASES=${PHASES#*PHASES=(}
PHASES=${PHASES%%)*}
# Kept as a plain space-separated string, not a bash array: macOS's stock bash (3.2, pre-4.4) throws
# "unbound variable" under `set -u` merely from EXPANDING an empty array (a long-fixed bash bug,
# but this script must run unmodified on whatever bash `env bash` finds) - a plain string sidesteps
# the whole class of bug, since `for p in $PHASES` word-splits a set-but-empty string with no error.
TOTAL=0
for _p in $PHASES; do TOTAL=$((TOTAL + 1)); done
[ "$TOTAL" -gt 0 ] || echo "warning: could not parse PHASES=(...) out of $HERE/tools/full-build.sh - phase list will be empty" >&2

TTY=0; [ -t 1 ] && TTY=1
c() { [ "$TTY" -eq 1 ] && printf '\033[%sm' "$1" || true; }  # ANSI colour, no-op when piped
RESET=$([ "$TTY" -eq 1 ] && printf '\033[0m' || true)

# HH:MM:SS (today, UTC - matches full-build.sh's own `_ts`) -> epoch seconds, or empty if
# unparsable. Tries GNU date's `-d` first (the sandbox/Linux side), falls back to BSD/macOS
# date's `-j -f` (a Mac watching the mirrored logs directly has no GNU coreutils by default).
epoch_of() {
  local today; today=$(date -u +%Y-%m-%d)
  date -u -d "$today $1" +%s 2>/dev/null && return
  date -u -j -f "%Y-%m-%d %H:%M:%S" "$today $1" +%s 2>/dev/null || true
}

human_dur() {
  local s=$1
  if [ "$s" -ge 3600 ]; then printf '%dh%02dm%02ds' $((s/3600)) $(((s%3600)/60)) $((s%60))
  elif [ "$s" -ge 60 ]; then printf '%dm%02ds' $((s/60)) $((s%60))
  else printf '%ds' "$s"; fi
}

# rdfc2im_pipeline loops translate/fetch/tsv/items over every source in turn, and fetching a
# source's full (unlimited) data is usually the single slowest part of the whole build - worth a
# per-source checklist, not just "the phase is running". Reads two lines full-build.sh already
# logs unconditionally: the one-time "sources: a b c" line (the exact list this run is using,
# whatever --sources/SOURCES was), and each source's own "-- SRC (scope=...) --" marker; a source
# counts as done once either another source's marker or the phase's closing `rdfc2im check` line
# appears after it. No new logging added to full-build.sh - purely inferred from what it already
# writes, so this can't drift out of sync with, or affect, the real build.
pipeline_sources() {
  # Every substitution below reads a marker that legitimately may not exist YET (a source not
  # started, `check` not reached) - that is a normal mid-build state, not an error, so each is
  # `|| true`-guarded: under `set -e -o pipefail`, an un-guarded `var=$(grep ... | ...)` whose
  # grep matches nothing kills the whole script, even though nothing here actually failed.
  local src_list; src_list=$(grep '\] sources: ' "$LOGFILE" 2>/dev/null | head -1 | sed 's/.*\] sources: //') || true
  [ -n "$src_list" ] || return 0

  local check_line; check_line=$(grep -n '\[rdfc2im_pipeline\] + python3 -m rdfc2im check$' "$LOGFILE" 2>/dev/null | head -1 | cut -d: -f1) || true
  check_line=${check_line:-0}

  local s marker_line later_marker_line
  for s in $src_list; do
    marker_line=$(grep -n "\[rdfc2im_pipeline\] -- $s (scope=" "$LOGFILE" 2>/dev/null | head -1 | cut -d: -f1) || true
    if [ -z "$marker_line" ]; then
      printf '        %s[ ]%s %s\n' "$(c 2)" "$RESET" "$s"
      continue
    fi
    later_marker_line=$(grep -n '\[rdfc2im_pipeline\] -- ' "$LOGFILE" 2>/dev/null \
      | awk -F: -v ml="$marker_line" '$1>ml{print $1; exit}') || true
    if [ -n "$later_marker_line" ] || { [ "$check_line" != "0" ] && [ "$check_line" -gt "$marker_line" ]; }; then
      printf '        %s[x]%s %s\n' "$(c 32)" "$RESET" "$s"
    else
      printf '        %s[>]%s %s\n' "$(c 33)" "$RESET" "$s"
    fi
  done
}

render() {
  local now current_phase done_count=0 build_start_epoch build_now_epoch
  now=$(date +%s)
  current_phase=$(cat "$STATUS_FILE" 2>/dev/null || echo "")

  [ "$TTY" -eq 1 ] && printf '\033[H\033[2J'   # cursor home + clear, instead of `clear` (no
                                                 # external dependency, no flash between frames)
  printf 'full-build.sh - run %s\n' "$RUN_ID"
  printf 'log: %s\n' "$LOGFILE"

  local first_ts; first_ts=$(head -1 "$LOGFILE" 2>/dev/null | awk '{print $1}')
  if [ -n "$first_ts" ]; then
    build_start_epoch=$(epoch_of "$first_ts")
    if [ -n "$build_start_epoch" ]; then
      build_now_epoch=$(epoch_of "$(date -u +%H:%M:%S)")
      printf 'elapsed: %s\n' "$(human_dur $(( build_now_epoch - build_start_epoch )) )"
    fi
  fi
  echo

  local p secs start_ts start_epoch elapsed
  for p in $PHASES; do
    if [ -f "$TIMING_TSV" ] && awk -F'\t' -v p="$p" '$1==p{found=1} END{exit !found}' "$TIMING_TSV" 2>/dev/null; then
      secs=$(awk -F'\t' -v p="$p" '$1==p{v=$3} END{print v}' "$TIMING_TSV")
      done_count=$((done_count + 1))
      printf '  %s[x]%s %-28s done (%s)\n' "$(c 32)" "$RESET" "$p" "$(human_dur "$secs")"
      [ "$p" = "rdfc2im_pipeline" ] && pipeline_sources
    elif [ -n "$current_phase" ] && [ "$p" = "$current_phase" ]; then
      start_ts=$(grep "\[$p\] ===== START =====" "$LOGFILE" 2>/dev/null | tail -1 | awk '{print $1}') || true
      if [ -n "$start_ts" ] && start_epoch=$(epoch_of "$start_ts") && [ -n "$start_epoch" ]; then
        elapsed=$(( $(epoch_of "$(date -u +%H:%M:%S)") - start_epoch ))
        printf '  %s[>]%s %-28s running (%s so far)\n' "$(c 33)" "$RESET" "$p" "$(human_dur "$elapsed")"
      else
        printf '  %s[>]%s %-28s running\n' "$(c 33)" "$RESET" "$p"
      fi
      [ "$p" = "rdfc2im_pipeline" ] && pipeline_sources
    else
      printf '  %s[ ]%s %-28s pending\n' "$(c 2)" "$RESET" "$p"
    fi
  done

  echo
  printf '%s/%s phases done\n' "$done_count" "$TOTAL"
  echo
  echo "-- last 12 log lines --"
  tail -n 12 "$LOGFILE" 2>/dev/null
}

if [ "$ONCE" -eq 1 ]; then
  render
  exit 0
fi

trap 'printf "%s\n" "$RESET"; exit 0' INT TERM
while true; do
  render
  sleep "$INTERVAL"
done
