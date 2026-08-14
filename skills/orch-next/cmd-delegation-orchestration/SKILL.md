---
name: cmd-delegation-orchestration
version: "1.0.0"
description: Use for cross-session Hermes control; not direct solo work.
author: ORCH-Next contributors and Hermes Agent
license: MIT
metadata:
  hermes:
    category: orch-next
    tags: [orch-next, operations, hermes-exclusive]
    ownership_manifest: "maestro-kernel:research/mk675/fable5_decision_os/mk737_p1a_skill_distribution_ownership.json"
---

# Cmd Delegation Orchestration Skill

Admit the destination by current role, goal, scope, active work, worktree, write set, and capability instead of cwd history.

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

## CMD epoch and exact-once return vocabulary

Every app-bound CMD handoff carries one `cmd_epoch_id` and, when rebased,
the `previous_epoch_id` it supersedes. `cmd_release_state` records whether the
current epoch is released before another CMD may acquire it, and
`checkpoint_sha256` binds the read checkpoint so an old state cannot be
replayed. Each `pending_returns[]` item is consumed at most once and records
the consuming epoch in `pending_returns[].consumed_by`. An acquire or consume
attempt against an unreleased, stale, or mismatched epoch returns the typed
blocker `CMD_EPOCH_CONFLICT`; it never silently switches writers or asks the
user to relay the return.

## Authority input contract

Routine `build`, `claim_checks`, `job_lifecycle`, `local_patch`,
`nonprotected_validation`, `normal_model_routing`, `ordinary_branch_or_pr_work`,
`read_only`, and `test` operations classify Maestro Authority as
`INAPPLICABLE` with `continuation_allowed=true`; CMD keeps the operation in
Hermes and does not call Authority for them.

The dispatcher derives that disposition from the required exact
`operation_class` allowlist. A caller-supplied disposition never overrides the
derived class. Protected classes route to a typed Maestro handoff with
`launch=false`; they never reach direct Codex. Routine classes alone are
`INAPPLICABLE`. If the authority integration is missing, do not silently fall back to Maestro/Codex execution.

Only these exact protected classes classify Maestro Authority as `REQUIRED`:
`credential_oauth_or_secret_mutation`, `paid_provider_use`,
`public_deploy_or_release`, `destructive_action`, `protected_integration`,
`authority_transfer`, `shared_security_or_runtime_mutation`,
`rollback_promotion`, and `final_acceptance`:

1. Load the exact immutable `HERMES_MAESTRO_AUTHORITY_BUNDLE_V3` identity,
   version `hermes-maestro-authority-bundle.v3`, and digest
   `7d6bc36e50938f74ad2728ed3d87f272620086de7bfd928616c84bbdfd09412e`
   from the admitted repository authority surface.
2. Consume the typed result from the validator named by that bundle.
3. Bind the result to the current goal, operation, target, and source revision.
4. If the bundle, digest, validator, or binding is absent or invalid, return
   `maestro_authority_unavailable` for that protected decision and continue
   every disjoint unprotected Hermes operation.

Never recreate the authority rule in this skill, self-authorize, ask Maestro to
execute routine work, or silently substitute a provider. Protected handoff
remains fail-closed and exact-operation-bound.

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

## Procedure

1. Restate the normal-user capability this operation will make possible and
   the evidence layer it can actually establish.
2. Reuse a capable active Hermes session, avoid duplicate owners, and consume every worker return without user relay.
3. Use native Hermes tools such as `read_file`, `search_files`, `terminal`,
   `patch`, `delegate_task`, `cronjob`, or `vision_analyze` only when
   they are available and necessary for this skill.
4. Keep source implementation, integration, installed adoption, fresh-session
   selection, runtime reachability, exercised behavior, user acceptance,
   effectiveness, and final completion separate.
5. Return the capability delta, blocker delta, exact owner and write set,
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

Do not retry unchanged protected failures, manufacture work to fill a cohort,
or use the user as a manual task/session router.

## Verification

Verify the requested behavioral delta through the repository-prescribed checks
and, where applicable, the normal Hermes operation. A local document, source
PASS, test PASS, audit, or installed file never proves a higher evidence layer.
