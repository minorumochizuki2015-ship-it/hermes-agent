---
name: demo-video-production
version: "1.0.0"
description: Use for truthful product demos; not release claims.
author: ORCH-Next contributors and Hermes Agent
license: MIT
metadata:
  hermes:
    category: orch-next
    tags: [orch-next, operations, hermes-exclusive]
    ownership_manifest: "maestro-kernel:research/mk675/fable5_decision_os/mk737_p1a_skill_distribution_ownership.json"
---

# Demo Video Production Skill

Plan, capture, assemble, and verify a product demo while separating source, runtime, and acceptance claims.

This is a self-contained Hermes runtime contract. It does not create another
plugin, installer, bundle, configuration, schema, queue, or control family.

## Maestro authority boundary

Hermes owns this operation. When it reaches a protected transition or
claim-sensitive decision, consume the current versioned Maestro authority
bundle and its typed validator result. If that authority input is unavailable,
return `maestro_authority_unavailable` for the protected decision while
continuing disjoint safe work. Maestro is never an execution fallback.

Authority consumption remains an integration dependency. This source skill
does not claim that an authority adapter or validator executed. Until the
integration surface supplies a current, target-bound typed result, every
authority-required operation returns `maestro_authority_unavailable` with
`launch=false`.

## When to use

Use when the current ORCH-Next goal directly needs this capability. Do not
select it for a similarly named authority-only decision or for evidence work
that cannot change the current implementation or user outcome.

## Prerequisites

- Read the active repository instructions and exact cwd, worktree, branch,
  revision, dirty state, goal, owner, and write boundary.
- Use current repo-local source as truth. Treat historical packets and external
  memory as advisory unless the active authority index cites them.
- Preserve one writer per worktree or durable record. Use separate worktrees
  and disjoint writes for parallel workers.
- Never persist raw prompts, hidden reasoning, terminal logs, secrets,
  credentials, private payloads, or provider payloads.
- `ffmpeg` and `ffprobe` must be on PATH for assembly and media inspection.
  Missing tools return `media_tool_unavailable`; they do not authorize a
  provider, upload, release, or alternate execution harness.

## Procedure

1. Restate the normal-user capability this operation will make possible and
   the evidence layer it can actually establish.
2. Write a shot list that maps each requested claim to one observable product
   operation and excludes unsupported hosted, production, or acceptance claims.
3. Capture each approved operation on the exact admitted route. Use `terminal`
   to inspect clips with `ffprobe`; reject clips with wrong dimensions,
   missing audio, secrets, notifications, private data, or misleading state.
4. Create an ordered plain-text clip list and run `terminal` with
   `python scripts/assemble_demo.py --list <file> --output <video>`.
   Use `--audio <file>` only for an admitted narration/music asset. The
   helper calls `ffmpeg` without a shell and emits sanitized typed JSON.
5. Inspect the assembled video from start to finish, verify audio/video
   duration and legibility, then produce truthful title, captions, and
   non-claims. Do not upload, publish, or submit without the exact typed
   protected-transition result.
6. Use native Hermes tools such as `read_file`, `search_files`, `terminal`,
   `patch`, `delegate_task`, `cronjob`, or `vision_analyze` only when
   they are available and necessary for this skill.
7. Keep source implementation, integration, installed adoption, fresh-session
   selection, runtime reachability, exercised behavior, user acceptance,
   effectiveness, and final completion separate.
8. Return the capability delta, blocker delta, exact owner and write set,
   checks run, rollback, next Hermes action, and precise non-claims.

## Failure behavior

- `hermes_runtime_unavailable`: the required Hermes operational surface is
  unavailable; return `launch=false` and do not substitute Maestro or Codex
  execution.
- `maestro_authority_unavailable`: the required protected-decision input is
  absent, stale, invalid, or unbound; return `launch=false`.
- `protected_transition_denied`: the typed authority result denies the exact
  transition; return `launch=false` and continue only disjoint safe work.
- `operation_scope_conflict`: another writer owns the same worktree, write
  set, or shared runtime; return `launch=false`.
- `media_tool_unavailable`: `ffmpeg` or `ffprobe` is absent.

Do not retry unchanged protected failures, manufacture work to fill a cohort,
or use the user as a manual task/session router.

## Verification

Verify the requested behavioral delta through the repository-prescribed checks
and, where applicable, the normal Hermes operation. A local document, source
PASS, test PASS, audit, or installed file never proves a higher evidence layer.
