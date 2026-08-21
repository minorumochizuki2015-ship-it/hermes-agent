---
name: skill-lifecycle
version: "0.1.0"
description: Own the complete Maestro Kernel skill lifecycle: structural health, recurrence monitoring, quality disposition, distribution, and cross-surface ecosystem audit.
source: maestro_kernel_t202_inc180_consolidation
trust_level: repo_local_accepted
activation: manual_contextual
permissions: {"filesystem":"read_only_by_default","network":"deny","credentials":"deny"}
mutation_policy: {"canonical_changes_require_scoped_authority":true,"plugin_mirror_is_generated":true,"user_and_cache_roots_are_read_only":true}
---

# skill-lifecycle

Use this skill when creating, checking, monitoring, repairing, merging,
quarantining, retiring, distributing, or auditing a Maestro Kernel skill. It is
the single owner for the duties formerly split across `skill-health-check`,
`skill-health-monitor`, `skill-quality-review`, and `ecosystem-audit`.

Do not resolve this skill from legacy `meta-agent`. Do not hand-copy the plugin
surface. Canonical source is `skills/`; the repository plugin mirror is
generated from it.

## Trigger and scope

Select this skill when any of these conditions is present:

- a required canonical or plugin skill may be missing, malformed, stale, or
  legacy-only;
- a skill process has repeated misses, user-discovered gaps, stale automation,
  or idle-after-partial behavior;
- skills overlap, drift, need repair, merge, quarantine, retirement, or
  promotion;
- a requirement, validator, fixture, package/CI entrypoint, task/checklist,
  handoff, plugin source, installed cache, or user-level surface may be
  disconnected;
- a Decision OS or Codex Security capability claim needs distribution-state
  separation.

This is a Claim Check and lifecycle owner, not an Authority Gate. A failure
blocks only the unsupported skill-health, distribution, promotion, firing, or
closeout claim unless an independently defined protected transition applies.

## Structural health

Check and report:

- required canonical `SKILL.md` exists with usable frontmatter;
- required skill instructions use current-source wording, verified against the
  current repo-local authority and source artifacts rather than a stale,
  superseded, or retrospective description;
- required plugin mirror file exists and is byte-identical;
- required trigger and fail-closed language exists;
- retired, successor-only, and legacy-only sources are not treated as current;
- the validator covers missing, plugin-missing, drift, and legacy-only cases;
- each surviving frontmatter skill name resolves exactly once in canonical
  source and exactly once in the generated repository mirror.

Return pass/fail with exact paths, hashes when relevant, and remediation owner.

## Recurrence and monitor health

Collect these decision-useful metrics:

- repeated miss count;
- user-discovered gap count;
- avoidable cost driver;
- missing trigger;
- missing validator;
- recurrence risk;
- next escalation;
- automation prompt-coverage freshness for a monitor, heartbeat, follow-up,
  recurring audit, or cross-session automation;
- blocker taxonomy: `progress_blocker`, `closeout_blocker`,
  `release_blocker`, `cleanup_blocker`, or `claim_boundary_only`;
- whether a `PARTIAL` result left a dispatchable next slice idle.

Classify a remaining blocker before follow-up. A contained wrong-thread,
wrong-repo, or cleanup issue is not a `progress_blocker` unless it can mutate
or prevent the current work surface. For an accepted `PARTIAL`, state either
`work_continuation_allowed=true` with `progress_blockers=[]` and a next target,
or the exact progress blocker.

- `release_blocker`: blocks stage, commit, push, deploy, release, or route
  promotion only.
- `cleanup_blocker`: blocks deletion, revert, quarantine removal, or
  disposition claims only.
- `claim_boundary_only`: does not block work, but prevents words such as
  `complete`, `ready`, `accepted`, `operational PASS`, or `production_ready`.

An automation's `ACTIVE` status is not health. Before a no-finding or
`DONT_NOTIFY`, compare its saved prompt/state with the latest user-led goal,
critical incidents, current task/checklist/recovery state, accepted audit
method, escape hatch, stop condition, and stale-evidence prohibition. Record
automation id, status, schedule, target, prompt-update evidence, required refs,
and freshness verdict.

Use:

```text
BLOCKED_FOR_AUTOMATION_PROMPT_COVERAGE_DRIFT
BLOCKED_FOR_MONITOR_ACTIVE_STATUS_SUBSTITUTING_FOR_CURRENT_AUDIT_METHOD
BLOCKED_FOR_USER_CORRECTION_REQUIRED_TO_REFRESH_MONITOR_PROMPT
```

Do not return no-finding from a stale prompt. Route recurring high-risk misses,
idle-after-partial nonfire, or user-discovered prompt drift to
`incident-response` and `incident-to-skill`; require a prompt-coverage
validator or negative fixture rather than a manual edit alone.

For `ui-design-direction-approval-pipeline`, measure time to first meaningful
candidate, user corrections, generic-template rejections, mock recurrence,
pre/post-approval violations, unnecessary firing, and post-implementation
rejections. Optimization may change candidate count, comparison format, skill
order, or evaluation axes, but may not weaken real-data boundaries, mock
prohibition, approvals, or an external Authority Gate. Report canonical,
mirror, installed cache, fresh-task selection, invocation, integration, and
observed-effective firing as separate states.

A sanitized observed-firing miss where a pre-approved post-implementation
continuation was treated as a simple read-only audit requires v0.1.1 trigger
clarification, a positive continuation fixture, and a transition-evidence row.
Do not expose task identifiers, private paths, or product judge UI material in
the health record.

## Lifecycle and quality decisions

## MK682 Repair Applied Contract

Compatibility markers: `keep/repair/quarantine/retire/create_new`,
`current_inventory.json`, `before/after or expected delta`, and
`idle-after-partial status`.

Use only:

```text
keep
repair
quarantine
retire
create_new
```

For `repair`, synchronize
`research/mk675/registers/current_inventory.json` and distinguish
`repair_classified` from `repair_applied`. Do not claim applied until the
canonical patch, generated mirror, and validator evidence exist. Retirement
requires evidence and a named successor; do not guess.

Review overlapping closeout ownership, dedupe key, owner boundary, trigger
quality, validator coverage, negative fixture, rollback, and promotion scope.
Do not allow two skills to own the same closeout decision without a dedupe key,
owner boundary, and validator.

For user-experience drift, require a named metric, before/after or expected
delta, primary evidence, forbidden substitutes, and claim-boundary resolution
phase.

Use:

```text
BLOCKED_FOR_SKILL_REPAIR_WITHOUT_LIFECYCLE_CLASSIFICATION
BLOCKED_FOR_REPAIR_CLASSIFIED_OVERCLAIMED_AS_APPLIED
BLOCKED_FOR_SKILL_PROMOTION_SOURCE_NOT_UPDATED
BLOCKED_FOR_PLUGIN_CACHE_ONLY_SKILL_FIX
BLOCKED_FOR_SKILL_REVIEW_WITHOUT_QUANTIFIED_CRITERIA
BLOCKED_FOR_SKILL_TRIGGER_PIPELINE_MISSING
```

## Cross-surface ecosystem audit

Audit the complete connection without turning support evidence into a broad
gate:

- requirement text has an executable validator;
- validator has positive and negative fixtures;
- canonical skill and generated plugin mirror both exist;
- package script exposes the validator;
- CI entrypoints expose the validator when that wiring is required;
- task, checklist, status, and register artifacts bind the same scope;
- downstream repos have an explicit handoff or non-claim boundary.

Do not accept a governance update that changes one surface while current
runtime or closeout surfaces still point to a missing or legacy skill. Record
unowned consumer migration as backlog rather than silently rewriting it.

## Distribution and promotion completeness

Marker: `MK728_SKILL_PLUGIN_DISTRIBUTION_HEALTH_REQUIRED`

Review these states separately:

1. canonical repo skill source;
2. generated repository plugin mirror;
3. installed plugin cache;
4. user-level overrides when relevant;
5. validator and negative fixture;
6. trigger pipeline and fresh-session exposure;
7. actual invocation and result consumption;
8. observed-effective prevention.

Canonical-only, plugin-only, docs-only, and installed-cache-only changes are
not distributed repairs. Installed cache status is
`not_verified_non_claim` unless inspected, and cache presence never proves a
fresh session selected or invoked the skill.

Use `promote_to_plugin` for cross-project rules affecting goal setup, audit
verdicts, routing, authority, or evidence substitution. Use
`quarantine_legacy_source` when an older source can restore weaker content.
User-level and installed-cache mutations require separate authority; this skill
only inventories and recommends them.

A green validator or suite is support/control evidence only. It does not prove
product progress, complete structural reform, observed-effective prevention,
runtime readiness, A3+ autonomy, or final/product/user acceptance.

## INC-178 Codex Security capability state

Marker: `INC178_CODEX_SECURITY_CAPABILITY_STATE_SEPARATION_REQUIRED`

Report separately:

- plugin config enabled;
- plugin cache present;
- required skill exposed in the current Available Skills or callable MCP
  surface;
- selected scan invocation;
- terminal manifest, findings, coverage, and report;
- result consumed into a selected action;
- observed-effective prevention in a later matching event.

Enabled configuration and cache presence do not prove exposure or invocation.
When `codex-security:security-diff-scan` is required but not exposed, use
`BLOCKED_FOR_CODEX_SECURITY_DIFF_SCAN_SESSION_EXPOSURE_UNAVAILABLE` only for
the protected adoption/security-completion transition. Preserve ordinary
bounded supervised, read-only, reversible repair, and targeted validation.
Do not mutate user-global configuration/cache or create a local replacement.

## MK733J-N Model-Neutral Decision OS

Trigger: a Decision OS skill changes an owner workflow or its distribution.
Run `scripts/ops/verify_mk733j_n_implementation.py --base-dir . --json` as
structural evidence and review task-class qualification, safe receipt linkage,
owner command/output consumer, shadow/enforce distinction, launcher/CI
coverage, independent audit, measured outcome fitness, canonical/mirror
parity, stop/escalation, and rollback.

Shadow permits diagnostics. Enforce content retains the owner command,
receipt/readback/activation boundary, rollback, and stop/escalation text.
Reject docs-only, canonical-only, plugin-only, marker-only, command-removed,
and installed-cache-only propagation. Roll back to synchronized generated
copies on drift and never claim automatic firing, runtime readiness, model
parity, or acceptance.

## Predecessor successor map and duty checklist

All predecessor selectors resolve to `skill-lifecycle`; the predecessor skill
directories are retired.

- [x] `skill-health-check` -> `Structural health`: canonical existence,
  frontmatter, current-source wording, mirror existence, trigger/fail-closed
  text, retired/legacy exclusion, validator missing/plugin/legacy coverage,
  exact-path output, MK728 distribution states, INC-178 state separation, and
  MK733J-N distribution/rollback review. Compatibility phrases:
  `canonical skill exists`, `plugin copy exists`, and `legacy-only sources`.
- [x] `skill-health-monitor` -> `Recurrence and monitor health`: all required
  miss/cost/trigger/validator/risk/escalation metrics, automation freshness,
  blocker taxonomy, idle-after-partial, continuation decision, cross-session
  blocker scoping, prompt self-drift fields and blockers, incident escalation,
  prompt-coverage fixture requirement, and UI-direction-pipeline metrics and
  state separation.
- [x] `skill-quality-review` -> `Lifecycle and quality decisions` plus
  `Distribution and promotion completeness`: lifecycle enum, repair
  classification/applied split, retirement evidence, overlap/dedupe ownership,
  promotion surfaces, plugin and legacy-source dispositions, quantified
  user-experience criteria, trigger pipeline, fail-closed markers, MK728
  distribution states, and MK733J-N owner-workflow review.
- [x] `ecosystem-audit` -> `Cross-surface ecosystem audit`: requirement to
  executable validator, positive/negative fixtures, canonical/mirror pair,
  package/CI exposure, task/checklist/status alignment, downstream
  handoff/non-claims, and rejection of one-surface governance updates.
  Compatibility phrases: `canonical skill and plugin copy`, `positive and
  negative fixtures`, and `package script`.

## Output and rollback

Return a `skill_lifecycle_review.v1` summary with trigger, lifecycle decision,
canonical paths, generated-mirror paths, hashes, validator/fixture result,
consumer/backlog gaps, installed/user-level non-claims, remediation owner,
rollback, and exact claim scope.

Rollback reference:
`restore_the_four_predecessor_skill_files_and_pre_generation_plugin_mirror_then_remove_skill_lifecycle`.
Rollback must restore a byte-consistent canonical/mirror pair and must not
mutate user-level roots or installed caches.
