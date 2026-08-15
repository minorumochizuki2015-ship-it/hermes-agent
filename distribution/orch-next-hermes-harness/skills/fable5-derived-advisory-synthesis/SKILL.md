---
name: fable5-derived-advisory-synthesis
version: "1.0.0"
description: Use for admitted Fable5 findings; not provider execution.
author: ORCH-Next contributors and Hermes Agent
license: MIT
metadata:
  hermes:
    category: orch-next
    tags: [orch-next, operations, hermes-exclusive]
    ownership_manifest: "maestro-kernel:research/mk675/fable5_decision_os/mk737_p1a_skill_distribution_ownership.json"
---

# Fable5 Derived Advisory Synthesis Skill

Map each allowed advisory finding to an existing phase, task, fixture, validator, patch, rollback, or concrete rejection reason.

## INC-191 full-fidelity consumer adoption

Marker: `INC191_FABLE5_FULL_FIDELITY_CONSUMER_ADOPTION_V1`.

After an admitted MK734/MK735 multi-file return, consume every supplied
implementation-card, checklist-predicate, negative-fixture, agent-role, and
method stable ID. Each stable ID has exactly one disposition:
`implemented`, `already_implemented_verified`, `active_owner`,
`repo_fact_contradiction`, or `protected_authority_blocker`. Cost-only,
optional-advice, generic-defer, and silent-omission dispositions are invalid.
An ID omitted by the source is a named `repo_fact_contradiction`, never an
invented finding.

Intake, traceability, or ledger coverage is not content consumption. At least
one admitted stable ID must produce a `content_implementation_return` that
binds the stable ID, existing target path, implemented decision primitive or
behavior, changed action or blocker, focused verification, rollback, and
nonclaims. `already_implemented_verified` is content-bearing only when it names
the exact existing path and verified behavior. An intake/ledger-only result
returns `BLOCKED_FOR_FABLE_CONTENT_RETURN_MISSING` and cannot claim adoption.

Narrow an `ADOPT` or `ADAPT` target only for one of four reasons: a concrete
repo-fact contradiction, a protected boundary, a verified duplicate, or a
concrete implementation hazard. Every narrowing binds evidence and an
equivalent replacement that preserves the same goal. Estimated cost may tune
model, effort, checkpoint, or deterministic-remainder routing; it cannot delete
architecture, premise challenge, counterhypothesis search, independent
challenge, integration ownership, acceptance separation, or learning before
natural execution evidence.

For repeated user-first detection, critical shared-runtime, cross-repository,
lifecycle, and first-natural-acceptance trains, preserve the full role contract:

- `AG-01`: exactly one operational controller and union synthesizer;
- `AG-02`: bounded implementation writers with disjoint ownership;
- `AG-03`: one train-level independent causal challenger that the controller
  cannot suppress because the blind spot may belong to that controller;
- `AG-04`: an always-owned serial integrator that consumes content returns;
- `AG-05`: acceptance exercised separately from the implementation writer;
- `AG-06`: sanitized train-close learning and comparable-case capture.

`support_work_progress_credit=0` denies direct product-progress credit; it is
not permission to remove a causal prevention dependency. Total cost includes
tokens/context, wall clock, review, rework, coordination, recurrence, user
correction, manual relay, capability delay, and discarded-provider work.
Unknown or unexercised cost stays `UNKNOWN`, never measured zero. Keep
`observed_effective_prevention=false` until a later comparable natural case
changes action before user correction.

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
2. Fable5 supplies design judgment only; it cannot establish repo truth, authority, provider execution, acceptance, or completion.
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
