#!/usr/bin/env bash
# Guard of the prumo. Registered on PreToolUse.
#
# A guarantee by hook, not by instruction: the model can get it wrong, and text injected
# into an item or a proposal can try. Exiting with code 2 blocks the call and hands the
# reason back to the model.
#
# SCOPE, and this is the delicate point of this guard:
#
#   * **merging a proposal is blocked**, unless the installation delegated it
#     (`forge.delegated_merge: true`) — and then still blocked while a review runs. No skill of this workspace merges on its own.
#   * **landing on a protected branch is blocked ALWAYS** — push, commit, rewrite — in any
#     repository under `paths.repositories`; `scripts/branch_guard.py` judges it, with the
#     list from `forge.protected_branches` (`homolog`, `main`, `master`, `prod`, `develop`
#     by default). The prumo's own checkout is not under that root, and is not guarded.
#   * **push, commit and moving the item are blocked ONLY during a review**, marked by
#     `.claude/.task-review-ativo`, which `review.py` creates and removes.
#   * **during a delivery** — one marker per delivery in `.claude/.prumo-deliver-ativo.d/`,
#     which `deliver.py` creates and removes — inside THAT delivery's worktree: a write
#     before its plan was approved, `git
#     add -A`, rewriting history, checkout; pushing its worktree or its branch from
#     anywhere; and moving the item. Outside every delivery's worktree nothing of this
#     applies: another agent's task, the workspace, a package. A commit WITH a pathspec
#     passes: one commit per step is the rule, and this is how it is kept.
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
DELIVER_MARKERS="${CLAUDE_PROJECT_DIR:-$PWD}/.claude/.prumo-deliver-ativo.d"
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
  age=$(( $(date +%s) - $(stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || echo 0) ))  # GNU, then BSD
  [ "$age" -lt "$VALIDITY_SECONDS" ]
}

# Which run the review marker belongs to, how old it is, and how it is let go — said
# with every block it causes, so a leftover is told apart from a live run.
review_held_by() {
  SCRIPTS="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}/scripts"
  python3 "$SCRIPTS/branch_guard.py" --pwd "$PWD" --command "" --held-by "$MARKER" 2>/dev/null
}

block() {
  echo "Blocked by the prumo's guard: $1" >&2
  echo "Approving is the ceiling. Merge, push and moving the item are a person's acts." >&2
  exit 2
}

# ---------- always, review running or not ----------
if [ -n "$cmd" ]; then
  if printf '%s' "$cmd" | grep -Eqi '(^|[[:space:]])(glab|gh)[[:space:]]+(mr|pr)[[:space:]]+merge|merge_requests?/[0-9]+/merge|/pulls?/[0-9]+/merge'; then
    # Never while a review runs, delegated or not. A delivery does not stop it: merging
    # another proposal touches no delivery's worktree.
    fresh "$MARKER" && block "merging a proposal while a review runs$(review_held_by)"
    # Otherwise only when the installation delegated it. The script answers, from the
    # project's directory so that a `prumo.yml` found by walking up is found; anything
    # but a clean 0 — no configuration, a crash — blocks.
    SCRIPTS="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}/scripts"
    why=$(cd "${CLAUDE_PROJECT_DIR:-$PWD}" 2>/dev/null && python3 "$SCRIPTS/branch_guard.py" --pwd "$PWD" --command "$cmd" --merge 2>/dev/null)
    [ $? -eq 0 ] || block "${why:-merging a proposal}"
  fi
  # Nothing lands directly on a protected branch — push, commit or rewrite — in any
  # repository the prumo works on. The list and the scope come from prumo.yml; the
  # judgement is a script, so it is tested like one.
  if printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])git([[:space:]]|$)'; then
    SCRIPTS="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}/scripts"
    reason=$(python3 "$SCRIPTS/branch_guard.py" --pwd "$PWD" --command "$cmd" 2>/dev/null)
    [ -n "$reason" ] && block "$reason"
  fi
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
  moves_the_item && block "moving the item during a review$(review_held_by)"
  # Only in a repository under `paths.repositories`: a review alters no history of the
  # repository it reads, and a commit in the prumo's own checkout, or anywhere else, is
  # not that. The same script that scopes the protected-branch rule answers here.
  if [ -n "$cmd" ] && printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])git([[:space:]]|$)'; then
    SCRIPTS="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}/scripts"
    inside=$(python3 "$SCRIPTS/branch_guard.py" --pwd "$PWD" --command "$cmd" --inside-repositories 2>/dev/null || echo yes)
    if [ "$inside" != "no" ]; then
      printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])git[[:space:]]+push([[:space:]]|$)' \
        && block "git push during a review$(review_held_by)"
      printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])git[[:space:]]+(commit|reset[[:space:]]+--hard|checkout[[:space:]]+-B|rebase|cherry-pick|revert)' \
        && block "a review does not alter the target repository's history$(review_held_by)"
    fi
  fi
fi

# ---------- while deliveries run ----------
# One marker per delivery. The judgement is a script: a write, or a git command, is
# judged by the delivery whose worktree it lands in, and by nobody outside every
# worktree — several agents deliver several items in one repository at once.
if ls "$DELIVER_MARKERS"/*.json >/dev/null 2>&1; then
  SCRIPTS="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}/scripts"
  why=$(python3 "$SCRIPTS/branch_guard.py" --delivery="$DELIVER_MARKERS" --tool="$tool" \
        --path="$path" --command="$cmd" --pwd="$PWD" 2>/dev/null)
  [ -n "$why" ] && block "$why"
fi

exit 0
