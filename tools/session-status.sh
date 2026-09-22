#!/usr/bin/env bash
# session-status.sh: report what a previous session left behind - never change anything itself.
#
# Docker containers, volumes, and everything under $HOME live in the sandbox VM, not in any one
# Claude Code session - they survive a session ending and are only gone if the sandbox itself is
# destroyed. Two real bugs came from exactly that: `make test` failing against a mine an earlier,
# unrelated session had left running (RESTART.md, tests/test_live_gating.py), and `rdfc2im project`
# picking up translate output from sources an earlier session had left in out/ (tests/test_check.py,
# test_project_covers_only_the_requested_sources_not_every_stale_directory).
#
# This prints a report and suggested commands. It never deletes, stops, or downs anything - the
# decision (keep the mine up? prune an old TRIAL_HOME? clean stale out/ dirs?) is always the
# person's, the same way this project's own process rule is "never push without asking".
#
# Usage:
#   tools/session-status.sh [--sources "a b c"]
#
#   --sources "a b c"   The demo panel to check out/ against (default: full-build.sh's own).
set -euo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)
cd "$HERE"

SOURCES=""
while [ $# -gt 0 ]; do
  case "$1" in
    --sources) SOURCES=$2; shift ;;
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
if [ -z "$SOURCES" ]; then
  SOURCES=$(sed -n 's/^SOURCES=\${SOURCES:-"\(.*\)"}$/\1/p' tools/full-build.sh)
fi

hr() { printf '%s\n' "----------------------------------------------------------------------"; }

echo "session-status: $HERE"
hr

echo "## Running containers (rdfc2im-*)"
if command -v docker >/dev/null 2>&1; then
  rows=$(docker ps -a --filter "name=rdfc2im-" --format '{{.Names}}\t{{.Status}}\t{{.Image}}' 2>/dev/null || true)
  if [ -n "$rows" ]; then
    printf '%s\n' "$rows" | column -t -s "$(printf '\t')"
    echo "A running mine here is not necessarily stale - it may be the one you want. If it is not:"
    echo "  docker compose -f trial/docker-compose.yml down       # stop, keep the loaded data"
    echo "  docker compose -f trial/docker-compose.yml down -v    # stop and discard it"
  else
    echo "(none)"
  fi
else
  echo "(docker not on PATH - skipped)"
fi
hr

echo "## Scratch build trees (TRIAL_HOME-shaped)"
# "Most recently used" is each directory's own newest file mtime, not a cross-reference into
# .build-logs - a build's log filename doesn't reliably encode which TRIAL_HOME it used (RUN_ID is
# freely chosen), and a stray background process touching old logs would silently mislead a
# lookup that trusted them (found live: an earlier session's own log-mirror loop, left running
# past the build it was started for, kept rewriting old runs' log files on every later build).
found=0
newest_dir="" newest_ts=0
for d in "$HOME"/intermine-build/trial_home*; do
  [ -d "$d" ] || continue
  found=1
  ts=$(find "$d" -type f -printf '%T@\n' 2>/dev/null | sort -rn | head -1)
  ts=${ts%.*}; ts=${ts:-0}
  [ "$ts" -gt "$newest_ts" ] && newest_ts=$ts && newest_dir=$d
done
for d in "$HOME"/intermine-build/trial_home*; do
  [ -d "$d" ] || continue
  tag=""; [ "$d" = "$newest_dir" ] && tag=" (most recently touched)"
  printf '  %-55s %6s%s\n' "$d" "$(du -sh "$d" 2>/dev/null | cut -f1)" "$tag"
done
[ "$found" -eq 1 ] || echo "(none under \$HOME/intermine-build)"
echo "Each is safe to 'rm -rf' - full-build.sh recreates whichever one it's pointed at."
hr

echo "## out/<source> directories outside the current demo panel ($SOURCES)"
# out/<source> mixes derived output (columns.tsv, raw/, items/, ...) with tracked, hand-curated
# state in the SAME directory: .gitignore negates mapping_subjects.tsv, mapping_predicates.sssom.tsv
# and their .base snapshots back into version control (they ARE the curation work). A flat
# `rm -rf out/<source>` deletes both alike - confirmed live: it took real, uncommitted curation
# work with it, recovered only because it had not been committed yet. `git clean -fdx` is the safe
# primitive instead: it only ever removes untracked/ignored files and refuses tracked content by
# construction, whatever the source directory holds.
stale=0
for d in out/*/; do
  s=$(basename "$d")
  [ "$s" = "_mine" ] || [ "$s" = "_docs" ] && continue
  case " $SOURCES " in *" $s "*) continue ;; esac
  [ -f "$d/columns.tsv" ] || continue      # only translate output counts, not an empty/tracked-only dir
  stale=1
  tracked=""
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1 && [ -n "$(git ls-files "$d")" ]; then
    tracked=" (also holds tracked curation files - see below)"
  fi
  printf '  %-20s %6s%s\n' "$s" "$(du -sh "$d" 2>/dev/null | cut -f1)" "$tracked"
done
[ "$stale" -eq 1 ] && echo "Gitignored derived output (translate/fetch/tsv/items) from a session that built a wider source
list. Doesn't break a scoped build (rdfc2im project/fetch/items take --source), but 'rdfc2im docs'
scans every out/ dir, so it will show up there too. Some of these directories may ALSO hold
mapping_subjects.tsv/mapping_predicates.sssom.tsv - tracked, hand-curated mapping state, not
derived output. NEVER 'rm -rf out/<source>' - use 'git clean -ndx out/<source>' to preview and
'git clean -fdx out/<source>' to actually remove: it only touches untracked/ignored files and
leaves any tracked curation work in place." \
  || echo "(none)"
hr

echo "## cached_raw_data/ health"
if [ -d cached_raw_data ] && [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python3 -m rdfc2im cache verify 2>&1 | sed 's/^/  /' || true
else
  echo "(no cached_raw_data/ or no .venv yet)"
fi
hr

echo "## git"
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")
dirty=$(git status --porcelain 2>/dev/null | grep -cv '^?? ' || true)
untracked=$(git status --porcelain 2>/dev/null | grep -c '^?? ' || true)
echo "  branch: $branch"
[ "$dirty" -gt 0 ] && echo "  $dirty tracked file(s) modified/staged - 'git status' for detail" || echo "  working tree clean (tracked files)"
[ "$untracked" -gt 0 ] && echo "  $untracked untracked path(s) at the repo root - 'git status --short' for detail"
if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
  ahead=$(git rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)
  [ "$ahead" -gt 0 ] && echo "  $ahead commit(s) not yet pushed - 'git push' when ready" || echo "  up to date with upstream"
else
  echo "  no upstream tracking branch configured - can't tell if commits are pushed"
fi
hr
echo "Nothing above was changed by this script."
