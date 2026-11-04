#!/usr/bin/env bash
# Guard of the review harness. Registered on PreToolUse.
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
#
# Without that scope the guard would break every skill that legitimately pushes, commits
# or moves an item in this workspace. A guarantee that runs over the rest of the
# environment is not a guarantee: it is a bug somebody will switch off on the first
# Friday.
set -uo pipefail

# The marker lives in the PROJECT under review, not in the plugin: one installation serves
# several workspaces, and the state "review in progress" belongs to one of them at a time.
# Without CLAUDE_PROJECT_DIR the hook has no project to guard and exits without blocking
# anything — failing open here is correct, because blocking a merge does not depend on the
# marker.
MARKER="${CLAUDE_PROJECT_DIR:-$PWD}/.claude/.task-review-ativo"
VALIDITY_SECONDS=14400   # 4h: a marker leaked by a crash must not wedge the workspace

input=$(cat)
read_field() {
  printf '%s' "$input" | python3 -c "
import json, sys
try: d = json.load(sys.stdin)
except Exception: sys.exit(0)
if '$1' == 'cmd': print((d.get('tool_input') or {}).get('command', ''))
else: print(d.get('tool_name', ''))
" 2>/dev/null
}

cmd=$(read_field cmd)
tool=$(read_field name)

review_active() {
  [ -f "$MARKER" ] || return 1
  local age
  age=$(( $(date +%s) - $(stat -c %Y "$MARKER" 2>/dev/null || echo 0) ))
  [ "$age" -lt "$VALIDITY_SECONDS" ]
}

block() {
  echo "Blocked by the review harness: $1" >&2
  echo "Approving is the ceiling of this skill. Merge, push and moving the item are a person's acts." >&2
  exit 2
}

# ---------- always, review running or not ----------
if [ -n "$cmd" ]; then
  printf '%s' "$cmd" | grep -Eqi '(^|[[:space:]])(glab|gh)[[:space:]]+(mr|pr)[[:space:]]+merge' \
    && block "merging a proposal from the CLI"
  printf '%s' "$cmd" | grep -Eqi 'merge_requests?/[0-9]+/merge|/pulls?/[0-9]+/merge' \
    && block "merging a proposal through the API"
fi

# ---------- only while a review is in progress ----------
review_active || exit 0

# The tracker is configurable, so this matches the ACTION a tool name describes, not one
# provider's spelling: `move_card_batch` on one board, `transition_issue` on another. A
# list of product names here would protect whoever wrote it and nobody else.
case "$tool" in
  *move_card*|*move_issue*|*move_ticket*|*transition*|*update_status*|*set_status*)
    block "moving the item during a review" ;;
esac

if [ -n "$cmd" ]; then
  printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])git[[:space:]]+push([[:space:]]|$)' \
    && block "git push during a review"
  printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])git[[:space:]]+(commit|reset[[:space:]]+--hard|checkout[[:space:]]+-B|rebase|cherry-pick|revert)' \
    && block "a review does not alter the target repository's history"
fi

exit 0
