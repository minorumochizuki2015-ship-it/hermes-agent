#!/usr/bin/env bash
# setup_lane.sh — create one git worktree + branch for a parallel Codex lane,
# forked from a base branch, with the shared runtime directory symlinked in
# (never copied — copying forks the data store and lanes stop sharing state).
#
# Usage:
#   setup_lane.sh <repo-root> <base-branch> <lane-branch-name> [runtime-dir-name]
#
# Optional lifecycle inputs (unknown values remain explicit OWNER_REVIEW state):
#   CODEX_PARALLEL_LANE_WRITE_SET (comma-separated paths)
#   CODEX_PARALLEL_LANE_TASK_ASSOCIATION
#   CODEX_PARALLEL_LANE_OWNER
#   CODEX_PARALLEL_LANE_RESULT_DESTINATION
#   CODEX_PARALLEL_LANE_RETENTION_EXPIRY (RFC3339; defaults to 30 days)
#
# Example:
#   setup_lane.sh /path/to/repo vm-p7-version-management kl-lane-9b .worklog-mcp-runtime
#
# Creates:
#   <repo-root>/.claude/worktrees/<lane-branch-name>   (new worktree, new branch)
#   with <runtime-dir-name> symlinked to <repo-root>/<runtime-dir-name>
#   (skipped if runtime-dir-name is omitted or the source doesn't exist —
#   not every project has a shared runtime directory to link)

set -euo pipefail

REPO_ROOT="${1:?repo-root required}"
BASE_BRANCH="${2:?base-branch required}"
LANE_NAME="${3:?lane-branch-name required}"
RUNTIME_DIR_NAME="${4:-}"

if ! REPO_REALPATH="$(cd "$REPO_ROOT" 2>/dev/null && pwd -P)"; then
  echo "BLOCKED_FOR_WORKTREE_CREATION_LIFECYCLE_FIELDS_UNAVAILABLE fields=repo_realpath" >&2
  exit 69
fi
if [[ -z "$BASE_BRANCH" || -z "$LANE_NAME" ]]; then
  echo "BLOCKED_FOR_WORKTREE_CREATION_LIFECYCLE_FIELDS_UNAVAILABLE fields=branch" >&2
  exit 69
fi
if ! BASE_SHA="$(git -C "$REPO_REALPATH" rev-parse --verify "$BASE_BRANCH^{commit}" 2>/dev/null)" || [[ -z "$BASE_SHA" ]]; then
  echo "BLOCKED_FOR_WORKTREE_CREATION_LIFECYCLE_FIELDS_UNAVAILABLE fields=base_sha" >&2
  exit 69
fi

LIFECYCLE_OWNER="${CODEX_PARALLEL_LANE_OWNER:-codex-parallel-lanes}"
LIFECYCLE_TASK_ASSOCIATION="${CODEX_PARALLEL_LANE_TASK_ASSOCIATION:-unknown}"
LIFECYCLE_WRITE_SET="${CODEX_PARALLEL_LANE_WRITE_SET:-UNKNOWN}"
LIFECYCLE_RESULT_DESTINATION="${CODEX_PARALLEL_LANE_RESULT_DESTINATION:-codex-parallel-lanes}"
LIFECYCLE_RETENTION_EXPIRY="${CODEX_PARALLEL_LANE_RETENTION_EXPIRY:-}"
if [[ -z "$LIFECYCLE_OWNER" || -z "$LIFECYCLE_TASK_ASSOCIATION" || -z "$LIFECYCLE_WRITE_SET" || -z "$LIFECYCLE_RESULT_DESTINATION" ]]; then
  echo "BLOCKED_FOR_WORKTREE_CREATION_LIFECYCLE_FIELDS_UNAVAILABLE fields=owner,write_set,dependency,result_consumption_destination" >&2
  exit 69
fi

WORKTREE_PATH="$REPO_REALPATH/.claude/worktrees/$LANE_NAME"
LIFECYCLE_RECORD_RELATIVE_PATH=".codex/decision-os/worktree-lifecycle.json"
LIFECYCLE_RECORD_PATH="$WORKTREE_PATH/$LIFECYCLE_RECORD_RELATIVE_PATH"
STAGING_DIR=""
STAGED_RECORD_PATH=""
LANE_CREATED=0
RECORD_INSTALLED=0

rollback_lane() {
  local exit_code=$?
  trap - EXIT
  if [[ "$LANE_CREATED" == "1" && "$RECORD_INSTALLED" != "1" ]]; then
    echo "ROLLING_BACK_UNRECORDED_LANE worktree=$WORKTREE_PATH branch=$LANE_NAME" >&2
    local rollback_failed=0
    if ! git -C "$REPO_REALPATH" worktree remove --force "$WORKTREE_PATH" >/dev/null 2>&1; then
      rollback_failed=1
    fi
    if git -C "$REPO_REALPATH" show-ref --verify --quiet "refs/heads/$LANE_NAME"; then
      if ! git -C "$REPO_REALPATH" branch -D "$LANE_NAME" >/dev/null 2>&1; then
        rollback_failed=1
      fi
    fi
    if [[ -e "$WORKTREE_PATH" || -L "$WORKTREE_PATH" ]] || git -C "$REPO_REALPATH" show-ref --verify --quiet "refs/heads/$LANE_NAME"; then
      rollback_failed=1
    fi
    if (( rollback_failed )); then
      echo "BLOCKED_FOR_WORKTREE_CREATION_ROLLBACK_INCOMPLETE worktree=$WORKTREE_PATH branch=$LANE_NAME" >&2
      exit_code=70
    fi
  fi
  if [[ -n "$STAGING_DIR" && -d "$STAGING_DIR" ]]; then
    rm -rf "$STAGING_DIR" 2>/dev/null || true
  fi
  exit "$exit_code"
}
trap rollback_lane EXIT

if git -C "$REPO_REALPATH" show-ref --verify --quiet "refs/heads/$LANE_NAME"; then
  echo "ERROR: branch '$LANE_NAME' already exists. Pick a unique lane name (branches are one-shot; delete the old one first if it's genuinely stale)." >&2
  exit 1
fi

if [[ -e "$WORKTREE_PATH" ]]; then
  echo "ERROR: worktree path already exists: $WORKTREE_PATH" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "BLOCKED_FOR_WORKTREE_CREATION_LIFECYCLE_FIELDS_UNAVAILABLE fields=lifecycle_record_writer" >&2
  exit 69
fi

if ! STAGING_DIR="$(mktemp -d "${TMPDIR:-/tmp}/codex-parallel-lane-record.XXXXXX" 2>/dev/null)" || [[ -z "$STAGING_DIR" ]]; then
  echo "BLOCKED_FOR_WORKTREE_CREATION_LIFECYCLE_FIELDS_UNAVAILABLE fields=lifecycle_record_staging" >&2
  exit 69
fi
STAGED_RECORD_PATH="$STAGING_DIR/worktree-lifecycle.json"
if ! python3 - "$STAGED_RECORD_PATH" "$REPO_REALPATH" "$WORKTREE_PATH" "$LANE_NAME" "$BASE_SHA" "$LIFECYCLE_WRITE_SET" "$LIFECYCLE_OWNER" "$LIFECYCLE_TASK_ASSOCIATION" "$LIFECYCLE_RESULT_DESTINATION" "$LIFECYCLE_RETENTION_EXPIRY" "$RUNTIME_DIR_NAME" <<'PY'
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def block(fields: str) -> "NoReturn":
    print(
        "BLOCKED_FOR_WORKTREE_CREATION_LIFECYCLE_FIELDS_UNAVAILABLE "
        f"fields={fields}",
        file=sys.stderr,
    )
    raise SystemExit(69)


if len(sys.argv) != 12:
    block("lifecycle_record_writer_arguments")

record_path = Path(sys.argv[1])
repo_realpath, worktree_realpath, branch, base_sha = sys.argv[2:6]
worktree_realpath = str(Path(worktree_realpath).resolve())
write_set_raw, owner, task_association = sys.argv[6:9]
result_destination, retention_expiry, runtime_dir_name = sys.argv[9:12]
if any(not value.strip() for value in (repo_realpath, worktree_realpath, branch, base_sha, owner, task_association, result_destination)):
    block("repo_realpath,worktree_realpath,branch,base_sha,owner,dependency,result_consumption_destination")

if write_set_raw == "UNKNOWN":
    write_set = ["UNKNOWN"]
else:
    write_set = [item.strip() for item in write_set_raw.split(",") if item.strip()]
    if not write_set:
        block("write_set")

if retention_expiry.strip():
    retention = retention_expiry.strip()
else:
    retention = (
        datetime.now(timezone.utc) + timedelta(days=30)
    ).isoformat().replace("+00:00", "Z")

runtime_effects = ["git_worktree_created"]
if runtime_dir_name:
    runtime_effects.append("shared_runtime_symlink_created")

record = {
    "repo_realpath": repo_realpath,
    "worktree_realpath": worktree_realpath,
    "branch": branch,
    "base_sha": base_sha,
    "dirty": False,
    "write_set": write_set,
    "runtime_effects": runtime_effects,
    "owner": owner,
    "lease": "lane-creation",
    "dependency": {"task_association": task_association},
    "first_checkpoint": "lane_created",
    "rollback": "single-commit revert",
    "result_consumption_destination": result_destination,
    "retention_expiry": retention,
    "disposition": "OWNER_REVIEW",
}
if record_path.exists():
    block("lifecycle_record_path_already_exists")
try:
    record_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = record_path.with_suffix(record_path.suffix + ".tmp")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(record_path)
except OSError:
    block("lifecycle_record_write")
PY
then
  echo "BLOCKED_FOR_WORKTREE_CREATION_LIFECYCLE_FIELDS_UNAVAILABLE fields=lifecycle_record" >&2
  exit 69
fi

# The lifecycle record is prepared before creation. Until its installation is
# confirmed, the EXIT trap treats every created lane as an unrecorded
# transaction and removes both the worktree and branch on any failure.
echo "Creating worktree '$WORKTREE_PATH' on new branch '$LANE_NAME' from '$BASE_BRANCH'..."
git -C "$REPO_REALPATH" worktree add -b "$LANE_NAME" "$WORKTREE_PATH" "$BASE_BRANCH"
LANE_CREATED=1

if [[ -n "$RUNTIME_DIR_NAME" ]]; then
  SRC="$REPO_REALPATH/$RUNTIME_DIR_NAME"
  DST="$WORKTREE_PATH/$RUNTIME_DIR_NAME"
  if [[ -e "$SRC" ]]; then
    rm -rf "$DST" 2>/dev/null || true
    ln -s "$SRC" "$DST"
    echo "Symlinked shared runtime: $DST -> $SRC"
  else
    echo "NOTE: runtime dir '$SRC' does not exist yet — skipped symlink. Create it in the repo root first if this lane needs shared state." >&2
  fi
fi

if ! WORKTREE_REALPATH="$(cd "$WORKTREE_PATH" 2>/dev/null && pwd -P)"; then
  echo "BLOCKED_FOR_WORKTREE_CREATION_LIFECYCLE_FIELDS_UNAVAILABLE fields=worktree_realpath" >&2
  exit 69
fi
if ! CREATED_HEAD="$(git -C "$WORKTREE_REALPATH" rev-parse --verify HEAD 2>/dev/null)" || [[ "$CREATED_HEAD" != "$BASE_SHA" ]]; then
  echo "BLOCKED_FOR_WORKTREE_CREATION_LIFECYCLE_FIELDS_UNAVAILABLE fields=base_sha" >&2
  exit 69
fi
if ! DIRTY_STATUS="$(git -C "$WORKTREE_REALPATH" status --porcelain=v1 --untracked-files=normal 2>/dev/null)" || [[ -n "$DIRTY_STATUS" ]]; then
  echo "BLOCKED_FOR_WORKTREE_CREATION_LIFECYCLE_FIELDS_UNAVAILABLE fields=dirty" >&2
  exit 69
fi

if [[ "${CODEX_PARALLEL_LANE_TEST_FAIL_RECORD_WRITE:-0}" == "1" ]]; then
  echo "BLOCKED_FOR_WORKTREE_CREATION_LIFECYCLE_RECORD_INJECTED_FAILURE" >&2
  exit 69
fi
if [[ -e "$LIFECYCLE_RECORD_PATH" || -L "$LIFECYCLE_RECORD_PATH" ]]; then
  echo "BLOCKED_FOR_WORKTREE_CREATION_LIFECYCLE_FIELDS_UNAVAILABLE fields=lifecycle_record_path_already_exists" >&2
  exit 69
fi
if ! mkdir -p "$(dirname "$LIFECYCLE_RECORD_PATH")" \
  || ! cp "$STAGED_RECORD_PATH" "${LIFECYCLE_RECORD_PATH}.tmp" \
  || ! mv -f "${LIFECYCLE_RECORD_PATH}.tmp" "$LIFECYCLE_RECORD_PATH" \
  || [[ ! -s "$LIFECYCLE_RECORD_PATH" ]]; then
  rm -f "${LIFECYCLE_RECORD_PATH}.tmp" 2>/dev/null || true
  echo "BLOCKED_FOR_WORKTREE_CREATION_LIFECYCLE_FIELDS_UNAVAILABLE fields=lifecycle_record" >&2
  exit 69
fi
RECORD_INSTALLED=1

echo "Lane ready: $WORKTREE_PATH (branch $LANE_NAME, base $BASE_BRANCH)"
echo "HEAD: $(git -C "$WORKTREE_REALPATH" rev-parse HEAD)"
echo "Lifecycle record: $LIFECYCLE_RECORD_PATH (disposition OWNER_REVIEW)"
