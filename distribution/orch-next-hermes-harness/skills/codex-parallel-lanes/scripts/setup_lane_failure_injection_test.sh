#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/codex-parallel-lane-record-atomicity.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

REPO="$TEST_ROOT/repo"
LANE="failure-injection-lane"
WORKTREE="$REPO/.claude/worktrees/$LANE"
mkdir -p "$REPO"
git init -q -b main "$REPO"
git -C "$REPO" config user.email fixture@example.invalid
git -C "$REPO" config user.name Fixture
git -C "$REPO" commit --allow-empty -qm fixture-base

set +e
CODEX_PARALLEL_LANE_TEST_FAIL_RECORD_WRITE=1 \
  "$SCRIPT_DIR/setup_lane.sh" "$REPO" main "$LANE" \
  >"$TEST_ROOT/stdout" 2>"$TEST_ROOT/stderr"
STATUS=$?
set -e

if [[ "$STATUS" != "69" ]] \
  || ! grep -q "BLOCKED_FOR_WORKTREE_CREATION_LIFECYCLE_RECORD_INJECTED_FAILURE" "$TEST_ROOT/stderr" \
  || ! grep -q "ROLLING_BACK_UNRECORDED_LANE" "$TEST_ROOT/stderr" \
  || [[ -e "$WORKTREE" || -L "$WORKTREE" ]] \
  || git -C "$REPO" show-ref --verify --quiet "refs/heads/$LANE"; then
  echo "FAIL WORKTREE_LANE_RECORD_ATOMICITY_FAILURE_INJECTION" >&2
  exit 1
fi

echo "PASS WORKTREE_LANE_RECORD_ATOMICITY_FAILURE_INJECTION"
