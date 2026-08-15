---
name: implementation-audit-dispatch
version: "1.0.0"
description: Use for independent patch audits; not implementation.
author: ORCH-Next contributors and Hermes Agent
license: MIT
metadata:
  hermes:
    category: orch-next
    tags: [orch-next, operations, hermes-exclusive]
    ownership_manifest: "maestro-kernel:research/mk675/fable5_decision_os/mk737_p1a_skill_distribution_ownership.json"
---

# Implementation Audit Dispatch Skill

Create a fixed-scope Hermes reviewer for behavior, safety, route, regression, and non-claim checks.

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

## Procedure

1. Restate the normal-user capability this operation will make possible and
   the evidence layer it can actually establish.
2. Keep the auditor distinct from the writer, read-only, fixed-revision, and unable to mutate or issue final user acceptance.
3. Use native Hermes tools such as `read_file`, `search_files`, `terminal`,
   `patch`, `delegate_task`, `cronjob`, or `vision_analyze` only when
   they are available and necessary for this skill.
4. Keep source implementation, integration, installed adoption, fresh-session
   selection, runtime reachability, exercised behavior, user acceptance,
   effectiveness, and final completion separate.
5. Return the capability delta, blocker delta, exact owner and write set,
   checks run, rollback, next Hermes action, and precise non-claims.

## Existing INC-191 return-consumption challenger

For the existing `INC-178`/`INC-191` recurrence, an implementation-audit
return is eligible for continuation only when the return is visible; the return
is consumed; and the result is decision-changing. The consumed return must name
the first normal-user
seam, one cause-changing owner, the exact write set, and the one changed next
action. A returned-but-unconsumed packet has no continuation credit. A consumed
`NO_ADDED_VALUE` result self-demotes and cannot justify an unchanged retry.
After repeated support-only returns, select the implementation owner and
cause-changing write set rather than another evidence loop.

Use one candidate for a unique deterministic fix. Tournament comparison may
select among hypotheses, but the default minimum-3 / 3-6 tournament remains
unpromoted without the existing accepted-task qualification. `standing non-UI
authority` is consumed by its current owner without user relay; protected user
gates remain protected and are not consumed by this support rule. Product work
continues while the read-only challenger runs, with
`support_work_progress_credit=0`. Structural PASS, parity, readback, or audit
evidence does not establish `observed_effective`; that claim remains false
until a later natural same-class recurrence is stopped before user correction.

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
