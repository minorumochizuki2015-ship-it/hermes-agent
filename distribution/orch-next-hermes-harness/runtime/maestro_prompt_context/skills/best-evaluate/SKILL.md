---
name: best-evaluate
version: "0.1.0"
description: Select the best next action from competing options by user-experience delta, visible-operation threshold, risk, cost, sub-agent need, and claim boundary.
---

# best-evaluate

Use this skill when choosing between competing implementation, audit,
requirement-definition, skill-migration, sub-agent, or product-visible work
lanes.

## Panoramic context precedes quantitative route

When `INC191_PANORAMIC_JOURNEY_ROOT_CAUSE_REFRAME_V1` is triggered, evaluate
the current decision only after the in-turn Panoramic Journey Board identifies
the first failing seam and whole-board ownership/propagation invariants. Reject
the next local-patch option while a source/runtime, upstream/downstream, or
visible-ready/loss-state contradiction remains. The board is transient
decision context, not a durable dashboard or external dependency.

After board classification, bind integer inputs `I,R,C,A,H,V,X,D,O` in `0..3`,
compute `TRS=max(0,2*I+2*R+C+A+H-V)` and
`PSS=X+V+D+(3-R)-C-O`, record hard-override reasons, selected model/effort,
writer count, parallel mode, checkpoint, process hard stop, and expected
user-capability delta. The existing executable dispatch envelope owns launch
enforcement; the Decision OS route result is advisory while its activation is
shadow.

This is the current Fable OS decision surface for best-action selection. It is
not a direct migration of legacy `meta-agent/skills/best-evaluate`, and it must
not revive legacy `decision-rail`, `e4`, or `e5` as default gates.

## Trigger

Use before selecting a next action when any of these are true:

- the user asks for "best", "evaluate", "compare", "which next", or similar;
- a user correction indicates intent drift, target misidentification, evidence
  substitution, or obvious non-user-beneficial work;
- the task has multiple plausible lanes, such as requirement quality, UI,
  voice, orchestration, skill migration, audit, support closeout, or runtime;
- a missing legacy skill, current skill gap, or sub-agent trigger could affect
  work selection;
- a validator, screenshot, DOM check, route setup, PR state, or support report
  could be mistaken for user-visible operation or acceptance.

## Required Decision Record

Record or cite a decision object before acting. Minimum fields:

1. `source_requirement_basis`
2. `user_intent`
3. `current_biggest_blocker`
4. `candidate_options`
5. `selection_criteria`
6. `selected_option`
7. `rejected_options_and_reasons`
8. `user_experience_delta`
9. `same_surface_visible_operation_threshold`
10. `sub_agent_trigger_status`
11. `cost_and_gate_burden`
12. `claim_boundary`
13. `next_validation`
14. `non_claims`

Candidate options must include the real product-visible lane when available
and at least one "hold / do not proceed" option when evidence is weak or the
target is uncertain.

## Selection Criteria

Rank options by these criteria in order:

1. direct improvement to the user's actual desired outcome;
2. whether the action makes the current Grand Goal more true;
3. whether the normal user surface can show the result on the same surface;
4. risk of preserving or increasing intent drift;
5. risk of turning support evidence into the work;
6. reversibility and scope;
7. cost and gate burden;
8. whether a sub-agent is needed, optional, or blocked.

Do not select an option only because it has a validator PASS, a clean route, a
draft PR, an audit packet, or easier implementation.

## Fail-Closed Rules

Block or downgrade the decision with the exact blocker when applicable:

- `BLOCKED_FOR_BEST_EVALUATE_DECISION_RECORD_MISSING`
- `BLOCKED_FOR_BEST_EVALUATE_USER_EXPERIENCE_DELTA_MISSING`
- `BLOCKED_FOR_BEST_EVALUATE_VALIDATOR_PASS_SELECTED_AS_DECISION`
- `BLOCKED_FOR_BEST_EVALUATE_LEGACY_META_AGENT_SELECTED`
- `BLOCKED_FOR_BEST_EVALUATE_SAME_SURFACE_THRESHOLD_UNBOUND`
- `BLOCKED_FOR_BEST_EVALUATE_SUB_AGENT_STATUS_UNBOUND`
- `BLOCKED_FOR_BEST_EVALUATE_SUPPORT_WORK_SELECTED_OVER_PRODUCT_VALUE`

If UI, media, voice, TOP Chat, route/session handoff, or connection status is
being judged, same-surface visible operation is required before candidate PASS,
readiness, or acceptance-style reporting. Validators, DOM checks, screenshots,
fixture tests, route setup, and hidden state do not substitute for this.

## Sub-Agent Boundary

If a sub-agent would materially improve accuracy, parallel research, or
independent critique, classify it as one of:

- `not_required_for_this_slice`
- `optional_support_only`
- `required_before_selection`
- `blocked_missing_trigger_contract`
- `blocked_missing_return_contract`

Do not claim sub-agent readiness from tool availability alone. A valid
sub-agent lane needs trigger condition, context packet, allowed tools, return
schema, timeout/failure behavior, and how the result changes the user-visible
decision.

## Output

Return the selected next action with:

- why it is best now;
- what user experience improves;
- what was rejected and why;
- what visible operation or deterministic verifier is required next;
- explicit non-claims.

Support, evidence, process, and skill work has `support_work_progress_credit=0`
unless it directly changes the normal user-visible experience and passes the
required same-surface operation.

## MK733J-N Model-Neutral Decision OS

Include qualified model route, risk ceiling, route alternatives, required escalation, and cost of unnecessary Sol escalation in the selected-action comparison. A model label or self-declared identity is never qualification evidence.

Trigger: two or more viable model-route/cost alternatives. Produce the selected/lower-cost/stop/wrong-lane comparison using `scripts/ops/mk733j_decision_os.py route --request ...`; shadow reports the candidate route, while enforce stops stale/over-risk choices. This owner supplies risk ceiling, unnecessary-Sol cost, and escalation reason to dispatch, with rollback to the lower-risk or stop option. It does not claim model parity, runtime firing, or user acceptance.

## MK747 Cognitive Shadow Decision Circuit

`MK747_COGNITIVE_SHADOW_DECISION_CIRCUIT_REQUIRED`

When a current high-impact decision includes a user correction, competing
content/support/stop options, or an innovation or feature-discovery
hypothesis, use the explicit callable shadow path:

```bash
python3 scripts/ops/mk_decision_preflight.py \
  --record <mk747-current-decision.json> \
  --cognitive-shadow \
  --json
```

The decision must bind current facts, counterevidence, uncertainty, the user
intent and correction, all candidate options, proposed rejection reasons, the
actual consumer and last mile, rollback, and a bounded experiment. The receipt
must cover IQ, EQ, UltraCode implementation judgment, overview judgment,
innovation, and discovery without accepting model self-scores.

Treat priors as disclosed bounded inputs: proposed or unmeasured priors have
zero influence; an accepted measured prior cannot exceed its cap or remove an
option. Evidence count has zero ranking influence. A correction must change
the recommended action, not only the explanation.

This path is always `shadow_only`. A missing fact, correction effect, consumer
chain, rollback, or bounded experiment withholds only the unsupported
recommendation; ordinary supervised work continues. Never use its receipt for
automatic gene promotion, authority mutation, runtime-firing claims, IQ/EQ
improvement claims, model qualification, product acceptance, or protected
integration.
