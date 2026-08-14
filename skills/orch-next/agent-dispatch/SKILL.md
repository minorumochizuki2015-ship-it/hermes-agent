---
name: agent-dispatch
version: "1.0.0"
description: Use when a worker changes the outcome; not routine work.
author: ORCH-Next contributors and Hermes Agent
license: MIT
metadata:
  hermes:
    category: orch-next
    tags: [orch-next, operations, hermes-exclusive]
    ownership_manifest: "maestro-kernel:research/mk675/fable5_decision_os/mk737_p1a_skill_distribution_ownership.json"
---

# Agent Dispatch Skill

Decide whether delegation changes the user-beneficial outcome, then define worker goal, context, tools, ownership, timeout, and return shape.

This is a self-contained Hermes runtime contract. It does not create another
plugin, installer, bundle, configuration, schema, queue, or control family.

## M-08 prompt-width budget envelope

Marker: `FABLE5_M08_PROMPT_WIDTH_BUDGET_ENVELOPE`.

For an advisory, research, causal-challenger, or architecture lane, the finite
Hermes dispatch envelope additionally binds:

- `full_grand_goal_included=true`;
- exactly one non-empty `exclusive_causal_question`;
- one or more explicit `counterhypotheses`;
- a typed `return_schema_ref`;
- `return_mode=digest_not_transcript` with bounded evidence citations;
- a context/cost budget whose unobserved usage remains `UNKNOWN`, never zero.

These fields adapt the Fable method into this existing Hermes skill. They do
not revive `meta-agent` or `orch-next-codex-harness` as an execution owner.
`NF-M08` rejects the affected dispatch with
`BLOCKED_FOR_FABLE5_NF_M08_PROMPT_WIDTH_INCOMPLETE` when any field is absent.
The controller may still choose deterministic current-owner work when a model
lane adds no value.

## Authority input contract

Routine `build`, `claim_checks`, `job_lifecycle`, `local_patch`,
`nonprotected_validation`, `normal_model_routing`, `ordinary_branch_or_pr_work`,
`read_only`, and `test` operations classify Maestro Authority as
`INAPPLICABLE` with `continuation_allowed=true`; the dispatcher continues in
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
execute routine work, or silently substitute a provider. A protected result is
bound to the exact operation and fails closed when unavailable; it is not an
execution route.

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
2. Use `delegate_task` for Hermes-native work; consume the result in the
   controller and classify completion, block, intentional stop, or crash
   without automatic protected retry. An explicitly selected Luna provider is
   dispatched through the codex-parallel-lanes direct launcher, not as a
   fallback after Hermes failure.
3. Use native Hermes tools such as `read_file`, `search_files`, `terminal`,
   `patch`, `delegate_task`, `cronjob`, or `vision_analyze` only when
   they are available and necessary for this skill.
4. Keep source implementation, integration, installed adoption, fresh-session
   selection, runtime reachability, exercised behavior, user acceptance,
   effectiveness, and final completion separate.
5. Return the capability delta, blocker delta, exact owner and write set,
   checks run, rollback, next Hermes action, and precise non-claims.

## PH-7 SDO reform routing adaptation

The reform permits safely disjoint ordinary Luna batches of 2-4 writers only
when every writer has its own worktree, exact non-overlapping write sets,
explicitly disjoint repository/runtime effects, and no superiority or default
promotion claim. A deterministic one-point fix remains one writer per
worktree/write set. If Luna is unavailable, return `USER_CMD_SWITCH_REQUIRED`,
queue the dispatch, and wait for the user to select an allowed CMD surface;
Sol, Terra, and UNKNOWN are not routine-writer substitutions.

Same-seam tournaments use two candidates, or three only for an explicit bounded
reason. The prospective 3-6-candidate policy is not a default without runtime
outcomes. The 12-comparable-accepted-task cohort is required only for Luna
superiority, cost-superiority, or permanent default-promotion claims.

CMD app-bound roles are nonwriters: `cmd_primary` is the manual
`fable5-ultra`/`ultra` Claude app route, while `cmd_alternate` is the manual
`gpt-5.6-sol`/`max` Codex app route with `auto_fallback=false`; both preserve
`writer_count=0`. Terra is fully retired from routing and must return
`BLOCKED_FOR_MK749_TERRA_ROUTE_RETIRED` if selected, actual, or invoked.

## Luna pace and service tier

The first meaningful-delta clock is a review point, never a hard worker stop.
For an INC-191 Luna dispatch, bind the actual consumer's `worker_pace` receipt:

- Luna High mechanical: grounding/material/no-delta reviews no earlier than
  5/10/20 minutes, with a 40-minute completion review;
- Luna Max precision: grounding/material/no-delta reviews no earlier than
  10/15/30 minutes, with a 60-minute completion review.

The CMD response checkpoint may not become the worker deadline. Elapsed time
alone cannot stop productive work. Replan from a material blocker, capability
or target change, or two cause-changing corrections. Patch/test syntax, path
resolution, receipt metadata, and grounding-with-no-diff do not consume that
budget.

Luna binds pace to the actual work class: `deterministic_mechanical` uses Luna
High and `precision_difficult` uses Luna Max. Precision work records
`service_tier_preference=fast` as a request preference. Only Max adds
`-c service_tier=fast` to the child argv. Runtime service tier remains
`UNKNOWN` unless a native Codex receipt proves it; `fast` is a service tier,
not a different model. Stop auto review remains unbound unless an executable
runtime binding exists, so `stop_auto_review_runtime_bound=false` is the
honest receipt until then.

Keep raw/cached/output/reasoning tokens, discount eligibility, effective cost,
elapsed time, first-pass result, rework, scope deviation, and normal-user
capability delta separate. Current Standard is the primary baseline. A
historical 80-percent reduction is the remaining factor `0.20`, and its Fast
comparison is `0.20 * 2.50 = 0.50` versus the pre-reduction baseline. Label
projections non-authoritative, keep exact billed cost `UNKNOWN` without billing
telemetry, and never demote Luna or replan from raw token volume alone.

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
