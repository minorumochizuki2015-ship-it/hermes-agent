---
name: heartbeat-cmd-control-guard
version: "1.0.0"
description: Use for transition-only continuity; not status polling.
author: ORCH-Next contributors and Hermes Agent
license: MIT
metadata:
  hermes:
    category: orch-next
    tags: [orch-next, operations, hermes-exclusive]
    ownership_manifest: "maestro-kernel:research/mk675/fable5_decision_os/mk737_p1a_skill_distribution_ownership.json"
---

# Heartbeat Cmd Control Guard Skill

Use existing Hermes scheduling and session state to detect meaningful idle, blocked, failed, or completed transitions.

This is a self-contained Hermes runtime contract. It does not create another
plugin, installer, bundle, configuration, schema, queue, or control family.

## Explicit user-action boundary

Permitted user operations are exactly:

1. Start the Fable CMD surface.
2. Switch to the Sol CMD surface during Claude quota limits.
3. Make protected authority decisions.
4. Give final acceptance.

The user must not copy worker reports, locate thread IDs, paste Luna results
between CMDs, watch stalled lanes, or press routine approval modals.
`USER_CMD_SWITCH_REQUIRED` refers only to the one-time app-surface selection;
it never asks the user to relay work or monitor routine progress.

## Maestro authority boundary

Hermes owns this operation. When it reaches a protected transition or
claim-sensitive decision, consume the current versioned Maestro authority
bundle and its typed validator result. If that authority input is unavailable,
return `maestro_authority_unavailable` for the protected decision while
continuing disjoint safe work. Maestro is never an execution fallback.

The source distribution binds the portable terminal contract
`INC191_PRE_IDLE_SUCCESSOR_ADMISSION_V1` version `1.1.0` to the exact Maestro
authority source and compact-profile digests recorded in `SOURCE_MANIFEST.json`.
The production gateway may consume an `ALLOW_*` result only from its protected
host transport after signature, authority binding, challenge, and replay
validation. The portable decision helper receives that admitted atomic result;
it never derives an `ALLOW_*` result from caller prose or local status fields.
Until that adapter supplies a current target-bound result, every
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

## Procedure

1. Restate the normal-user capability this operation will make possible and
   the evidence layer it can actually establish.
2. Prefer native event delivery. Use `cronjob` only as a bounded fallback
   reconciliation path when an event cannot provide the required transition.
3. Apply `state_transition_only`: unchanged state emits nothing. Apply
   `no_decision_delta_self_demotion`: if a checkpoint cannot change a
   decision, remove it from the active path.
4. Update an existing schedule instead of creating a duplicate. Bind
   `self_delete_at_terminal_or_obsolete` so completed or superseded monitors
   do not continue generating work.
5. Emit only cause-changing actions; never create an unbounded monitor or use
   the user as a session router. Monitoring has product-progress credit zero.
   At review, classify the control as `keep`, `demote`, or `remove`.
6. Apply `FABLE5_M10_TPL_H_QUESTION_6`: has this acceptance predicate failed
   twice, or has the user corrected the premise? If yes, do not schedule a
   third unchanged attempt. Return `STOP_AND_REPLAN` with
   `BLOCKED_FOR_FABLE5_NF_M10_SILENT_THIRD_ACCEPTANCE_ATTEMPT`, and bind the
   changed premise or exact blocker while disjoint safe work continues. A
   genuinely changed predicate or causal hypothesis is a new attempt, not an
   unchanged retry.
7. At an idle, final, or protected-wait transition, require the portable
   terminal decision to return exactly one of `ALLOW_FINAL_IDLE`,
   `ALLOW_NARROW_PROTECTED_WAIT`, `ALLOW_IDLE_AFTER_VERIFIED_SUCCESSOR`,
   `REJECT_IDLE_DISJOINT_WORK_UNASSIGNED`, or
   `CONTINUE_CURRENT_CONTROLLER`. Bind Grand Goal finality, the scoped
   protected seam, disjoint remaining work, exact next owner/slice, verified
   successor readback, and monotonic owner-transfer epoch. Never apply this
   terminal-only decision during ordinary patch/test work.
8. Use native Hermes tools such as `read_file`, `search_files`, `terminal`,
   `patch`, `delegate_task`, `cronjob`, or `vision_analyze` only when
   they are available and necessary for this skill.
9. Keep source implementation, integration, installed adoption, fresh-session
   selection, runtime reachability, exercised behavior, user acceptance,
   effectiveness, and final completion separate.
10. Return the capability delta, blocker delta, exact owner and write set,
   checks run, rollback, next Hermes action, and precise non-claims.

For INC-191 Luna implementation work, heartbeat timing is review-only. Luna
High mechanical work uses no-earlier-than 5/10/20-minute
grounding/material/no-delta reviews; Luna Max precision work uses 10/15/30.
The CMD response checkpoint is not the worker deadline, and elapsed time alone
cannot stop a productive worker. Count only a strategy, hypothesis, target,
write-set, or authority-boundary correction toward the two-correction replan;
do not count clerical patch/test/path/receipt corrections.

## CMD epoch and exact-once return vocabulary

Heartbeat readback preserves the active `cmd_epoch_id` and its
`previous_epoch_id` lineage. `cmd_release_state` must be released before a
successor acquires the CMD surface; `checkpoint_sha256` rejects a changed or
replayed checkpoint. A `pending_returns[]` entry is consumed once only, with
the consuming epoch recorded in `pending_returns[].consumed_by`. A stale,
unreleased, or checksum-mismatched continuation returns `CMD_EPOCH_CONFLICT`
and remains queued for the authorized CMD surface; it does not fall back to a
routine writer or use the user as a relay.


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

Do not retry unchanged protected failures, manufacture work to fill a cohort,
or use the user as a manual task/session router.

## Verification

Verify the requested behavioral delta through the repository-prescribed checks
and, where applicable, the normal Hermes operation. A local document, source
PASS, test PASS, audit, or installed file never proves a higher evidence layer.

Run `terminal` with
`python scripts/verify_heartbeat_cmd_control_guard.py` from this skill
directory to validate the portable runtime contract. The pure
`scripts/heartbeat_control.py` decision function covers start, unchanged,
transition, timeout, paused, duplicate, failed destination, bounded recovery,
`DONT_NOTIFY`, `never deliver a heartbeat`, and authority-required external
dispatch without performing the launch itself.
