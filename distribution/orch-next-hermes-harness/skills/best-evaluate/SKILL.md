---
name: best-evaluate
version: "1.0.0"
description: Use for unresolved option tradeoffs; not fixed decisions.
author: ORCH-Next contributors and Hermes Agent
license: MIT
metadata:
  hermes:
    category: orch-next
    tags: [orch-next, operations, hermes-exclusive]
    ownership_manifest: "maestro-kernel:research/mk675/fable5_decision_os/mk737_p1a_skill_distribution_ownership.json"
---

# Best Evaluate Skill

Compare candidate actions using user-capability delta, blocker delta, cost, risk, evidence quality, reversibility, and stop condition.

## Full-fidelity design comparison

Marker: `INC191_FABLE5_FULL_FIDELITY_CONSUMER_ADOPTION_V1`.

When an admitted MK734/MK735 program is the selected architecture, compare
model, effort, checkpoints, and sequencing without deleting its architecture,
premise challenge, counterhypothesis, independent challenger, integration,
acceptance, or learning roles. Projected token/context ceilings are calibration
inputs, not measured harm or cost-superiority proof. A narrower option is
admissible only for a repo-fact contradiction, protected boundary, verified
duplicate, or concrete implementation hazard, with evidence and an equivalent
replacement preserving the goal.

For an architecture-scale decision, bind each candidate and rejection to its
stable ID. Each supplied stable ID has exactly one disposition:
`implemented`, `already_implemented_verified`, `active_owner`,
`repo_fact_contradiction`, or `protected_authority_blocker`. Every rejected
option also records `option_id`, one of the four allowed reason classes,
concrete evidence, equivalent replacement when narrowing, and a
`resurrection_condition`. Require at least one `content_implementation_return`
that changes or verifies an existing implementation path; intake, ledger,
report, or status coverage alone fails
`BLOCKED_FOR_FABLE_CONTENT_RETURN_MISSING`.

Total cost includes tokens/context, wall clock, review, rework, coordination,
recurrence, user correction, manual relay, capability delay, and
discarded-provider work. Unknown is `UNKNOWN`, not zero.
`support_work_progress_credit=0` means no direct product capability was
delivered; it does not make a causally required prevention dependency
removable. Cost may tune model, effort, and checkpoints only. Preserve the full
`AG-01` through `AG-06` contract for every triggered critical train, including
controller-independent `AG-03`, and keep
`observed_effective_prevention=false` until a later comparable natural case
prevents the correction before the user supplies it.

The full contract is `AG-01` one controller/union synthesizer, `AG-02` bounded
disjoint writers, `AG-03` an independent causal challenger, `AG-04` an always-
owned serial integrator, `AG-05` acceptance separate from the writer, and
`AG-06` sanitized train-close learning and comparable-case capture.

This is a self-contained Hermes runtime contract. It does not create another
plugin, installer, bundle, configuration, schema, queue, or control family.

## Authority input contract

This skill implements only the operational half. Before any protected
transition, model-risk disposition, policy decision, validator verdict,
release/deploy/credential action, rollback promotion, claim promotion, or final
acceptance:

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
execute, or silently fall back to Maestro/Codex execution.

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
2. Select one operational action; authority admissibility and final claims come only from the versioned Maestro result.
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
