#!/usr/bin/env bash
# Guard of the prumo. Registered on PreToolUse.
#
# A guarantee by hook, not by instruction: the model can get it wrong, and text injected
# into an item or a proposal can try. Exiting with code 2 blocks the call and hands the
# reason back to the model.
#
# SCOPE, and this is the delicate point of this guard:
#
#   * **merging a proposal is blocked ALWAYS.** No skill of this workspace should merge.
#   * **push, commit and moving the item are blocked ONLY during a review**, marked by
#     `.claude/.task-review-ativo`, which `review.py` creates and removes.
#   * **during a delivery** — `.claude/.prumo-deliver-ativo`, which `deliver.py` creates
#     and removes — push, moving the item, `git add -A`, rewriting the base and any write
#     outside the worktree or outside the files the measurement named are blocked. A commit
#     WITH a pathspec passes: one commit per step is the rule, and this is how it is kept.
#
# Without that scope the guard would break every skill that legitimately pushes, commits
# or moves an item in this workspace. A guarantee that runs over the rest of the
# environment is not a guarantee: it is a bug somebody will switch off on the first
# Friday.
set -uo pipefail

# The markers live in the PROJECT, not in the plugin: one installation serves several
# workspaces, and the state "a run is in progress" belongs to one of them at a time.
# Without CLAUDE_PROJECT_DIR the hook has no project to guard and exits without blocking
# anything — failing open here is correct, because blocking a merge does not depend on
# the marker.
MARKER="${CLAUDE_PROJECT_DIR:-$PWD}/.claude/.task-review-ativo"
DELIVER_MARKER="${CLAUDE_PROJECT_DIR:-$PWD}/.claude/.prumo-deliver-ativo"
VALIDITY_SECONDS=14400   # 4h: a marker leaked by a crash must not wedge the workspace

input=$(cat)
read_field() {
  printf '%s' "$input" | python3 -c "
import json, sys
try: d = json.load(sys.stdin)
except Exception: sys.exit(0)
ti = d.get('tool_input') or {}
if '$1' == 'cmd': print(ti.get('command', ''))
elif '$1' == 'path': print(ti.get('file_path', '') or ti.get('path', ''))
else: print(d.get('tool_name', ''))
" 2>/dev/null
}

cmd=$(read_field cmd)
tool=$(read_field name)
path=$(read_field path)

fresh() {
  [ -f "$1" ] || return 1
  local age
  age=$(( $(date +%s) - $(stat -c %Y "$1" 2>/dev/null || echo 0) ))
  [ "$age" -lt "$VALIDITY_SECONDS" ]
}

block() {
  echo "Blocked by the prumo's guard: $1" >&2
  echo "Approving is the ceiling. Merge, push and moving the item are a person's acts." >&2
  exit 2
}

# ---------- always, review running or not ----------
if [ -n "$cmd" ]; then
  printf '%s' "$cmd" | grep -Eqi '(^|[[:space:]])(glab|gh)[[:space:]]+(mr|pr)[[:space:]]+merge' \
    && block "merging a proposal from the CLI"
  printf '%s' "$cmd" | grep -Eqi 'merge_requests?/[0-9]+/merge|/pulls?/[0-9]+/merge' \
    && block "merging a proposal through the API"
fi

moves_the_item() {
  case "$tool" in
    *move_card*|*move_issue*|*move_ticket*|*transition*|*update_status*|*set_status*) return 0 ;;
  esac
  return 1
}

# ---------- only while a review is in progress ----------
if fresh "$MARKER"; then
  # The tracker is configurable, so this matches the ACTION a tool name describes, not
  # one provider's spelling: `move_card_batch` on one board, `transition_issue` on another.
  moves_the_item && block "moving the item during a review"
  if [ -n "$cmd" ]; then
    printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])git[[:space:]]+push([[:space:]]|$)' \
      && block "git push during a review"
    printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])git[[:space:]]+(commit|reset[[:space:]]+--hard|checkout[[:space:]]+-B|rebase|cherry-pick|revert)' \
      && block "a review does not alter the target repository's history"
  fi
fi

# ---------- only while a delivery is in progress ----------
if fresh "$DELIVER_MARKER"; then
  moves_the_item && block "moving the item during a delivery"
  if [ -n "$cmd" ]; then
    printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])git[[:space:]]+push([[:space:]]|$)' \
      && block "git push during a delivery — handing the branch over is a person's act"
    printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])git[[:space:]]+add[[:space:]]+(-A|--all|\.)([[:space:]]|$)' \
      && block "git add without a pathspec — name the files"
    printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])git[[:space:]]+(reset[[:space:]]+--hard|rebase|cherry-pick|revert|merge)' \
      && block "a delivery does not rewrite history"
    printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])git[[:space:]]+checkout([[:space:]]|$)' \
      && ! printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+checkout[[:space:]]+--[[:space:]]' \
      && block "a delivery stays on its own branch"
  fi
  case "$tool" in
    Write|Edit|MultiEdit|NotebookEdit)
      verdict=$(python3 - "$DELIVER_MARKER" "$path" <<'PY'
import json, os, sys
marker, target = sys.argv[1], sys.argv[2]
try:
    m = json.load(open(marker, encoding="utf-8"))
except Exception:
    sys.exit(0)
worktree = os.path.realpath(m.get("worktree") or "")
allowed = list(m.get("allowed") or [])
# The files a write may touch are what the measurement named — written to the package
# by the skill between the two stages of the workflow, so the guard reads them from
# disk and asks nobody.
try:
    measure = json.load(open(os.path.join(m.get("package") or "", "measure.json"), encoding="utf-8"))
    allowed += list(measure.get("files") or [])
except Exception:
    pass
real = os.path.realpath(target or "")
if not worktree or not real.startswith(worktree.rstrip("/") + "/"):
    print("outside the worktree " + worktree); sys.exit(0)
rel = os.path.relpath(real, worktree)
if allowed and rel not in allowed:
    print("outside the named files: " + rel); sys.exit(0)
PY
)
      [ -n "$verdict" ] && block "a write $verdict"
      ;;
  esac
fi

exit 0
