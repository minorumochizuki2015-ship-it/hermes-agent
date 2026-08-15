---
name: skill-authoring
version: "1.0.0"
description: Use for new Hermes skills; not ordinary documentation.
author: ORCH-Next contributors and Hermes Agent
license: MIT
metadata:
  hermes:
    category: orch-next
    tags: [orch-next, operations, hermes-exclusive]
    ownership_manifest: "maestro-kernel:research/mk675/fable5_decision_os/mk737_p1a_skill_distribution_ownership.json"
---

# Skill Authoring Skill

Create concise self-contained skills with valid frontmatter, native Hermes tools, clear triggers, procedures, pitfalls, verification, and lifecycle boundaries.

This is a self-contained Hermes runtime contract. It does not create another
plugin, installer, bundle, configuration, schema, queue, or control family.

## M-06 red-first negative fixtures

Marker: `FABLE5_M06_RED_FIRST_ACCEPTANCE_FIXTURE`.

Before implementation, write or nominate the negative fixture for every claim
that will gate progress and record its RED result against current admitted
reality. A later green run supports the claim only when it uses the same
fixture identity and its red observation predates both implementation and the
green claim. A fixture authored with an already-passing claim, or a post-hoc
fixture without a red receipt, cannot gate that claim.

`NF-M06` returns `BLOCKED_FOR_FABLE5_NF_M06_RED_RUN_MISSING` for the affected
claim only. Hermes may continue ordinary implementation needed to establish
the red receipt; this is not a new Authority Gate.

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
2. Use the existing bundled tree and sync path; do not create another installer, plugin, schema, or authority family.
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
