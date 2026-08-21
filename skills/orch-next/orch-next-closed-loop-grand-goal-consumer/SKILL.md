---
name: orch-next-closed-loop-grand-goal-consumer
version: "1.0.0"
description: Consume the Maestro closed-loop Goal contract safely.
author: ORCH-Next contributors and Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    category: orch-next
    tags: [orch-next, operations, maestro-owned-projection]
    ownership_manifest: "maestro-kernel:research/mk675/fable5_decision_os/mk737_p1a_skill_distribution_ownership.json"
    related_skills: [grand-goal-implementation-orchestrator, goal-audit-checklist]
---

# Maestro Closed-Loop Grand Goal Consumer

This is the Hermes operational front-door consumer and projection for one
Maestro-owned Goal. It is not a second Goal authority, control, acceptance
owner, or generic orchestration family.

<!-- hermes_goal_consumer_contract_json: {"authority":{"binding_drift_result":"maestro_goal_binding_drift","bundle_digest":"7d6bc36e50938f74ad2728ed3d87f272620086de7bfd928616c84bbdfd09412e","bundle_identity":"HERMES_MAESTRO_AUTHORITY_BUNDLE_V3","bundle_version":"hermes-maestro-authority-bundle.v3","can_issue_protected_authority":false,"can_issue_user_acceptance":false,"can_perform_protected_transition":false,"launch":false,"unavailable_result":"maestro_authority_unavailable"},"authority_owner":"maestro-kernel","canonical_phase_gate_map":{"P0":[],"P1":[],"P10":["U1","U5","U6","U8"],"P11":["U8"],"P2":["U4"],"P3":["U4"],"P4":["U5"],"P5":["U2"],"P6":["U3","U6"],"P7":["U6"],"P8":["U7"],"P9":["U7"]},"canonical_source_binding":{"control":{"path":"controls/orch-next-closed-loop-grand-goal.v1.json","sha256":"11feb5ef9d35dea49c47905c8680ad14e70c058a0dad4b9d6d1387c5fbd63ddc"},"layer":"source_composed_candidate","repository":"maestro-kernel","revision":"70c61128b1f31b29ae2c1e2a53d676ffb62e4d9e","skill":{"path":"skills/orch-next-closed-loop-grand-goal/SKILL.md","sha256":"82064d2049309b8cd9f6a2b37be155a0b6a4ee78b01effc0b0fff759a40b3575"},"tree":"86f47770b0fac0ab13edf0d1bfc5930de1c0e62a"},"consumer_skill_id":"orch-next-closed-loop-grand-goal-consumer","final_acceptance":{"consecutive_natural_closed_loop_journeys":10,"critical_failures":0,"explicit_same_surface_user_acceptance":true,"manual_relay":0,"minimum_recovery_cases":3,"minimum_task_classes":3,"requires_all_u1_u8":true},"grand_goal_id":"ORCH_NEXT_CMD_MAESTRO_HERMES_SDO_PMS_ODG_CLOSED_LOOP_COMPLETION","lifecycle_order":["SOURCE","SOURCE_COMPOSED_CANDIDATE","CANONICAL_INTEGRATED","INSTALLED","SELECTED","LIVE","NATURALLY_EXERCISED","RESULT_CONSUMED","USER_ACCEPTED"],"normal_user_route":["U1","U2","U3","U4","U5","U6","U7","U8"],"operational_consumer_owner":"hermes-agent","role":"operational_front_door_consumer_projection","schema":"orch-next-closed-loop-grand-goal-consumer.v1","support_work_progress_credit":0} -->

## When to use

Select this skill only when the current Goal ID is exactly
`ORCH_NEXT_CMD_MAESTRO_HERMES_SDO_PMS_ODG_CLOSED_LOOP_COMPLETION`. Do not
select it for a similar architecture review, evidence-only task, unrelated
product operation, or another Grand Goal.

Before selection, require the exact Maestro repository revision, tree,
control path and digest, and canonical skill path and digest embedded above.
If any binding is absent or different, return `maestro_goal_binding_drift`
with `launch=false` for this Goal claim and selection only. Continue disjoint
safe work; do not create a new Gate or broaden the failure.

## Ownership and authority boundary

- Maestro owns the Goal control, canonical phase mapping, protected authority,
  completion threshold, and final acceptance boundary.
- Hermes may select this exact consumer, project the current phase, consume a
  typed Maestro decision, execute an already-authorized operation, resume or
  recover it, and return progress or results on the same user surface.
- This skill cannot issue an authority grant, perform a protected transition
  by itself, certify user acceptance, or promote any lifecycle layer.
- A protected transition requires the exact current
  `HERMES_MAESTRO_AUTHORITY_BUNDLE_V3` validator result bound to Goal,
  operation, target, and source. When absent, stale, denied, or unbound, return
  `maestro_authority_unavailable` with `launch=false` and continue only
  disjoint safe work.

## Normal-user route

Drive the first missing edge in the hard-AND route, without skipping layers:

1. U1 — a normal user submits one natural request at the Hermes/Codex front door.
2. U2 — UserPromptSubmit selects safe task-aware context.
3. U3 — Hermes naturally consumes that context and the current SDO decision.
4. U4 — Maestro decides only the exact protected transition and never executes it.
5. U5 — Hermes dispatches, executes, resumes, and recovers the authorized work.
6. U6 — progress, blockers, and the result remain visible on the same surface.
7. U7 — only accepted results enter sanitized PMS memory and advisory ODG projection.
8. U8 — a later natural task reuses accepted memory and decision state without relay.

At every checkpoint, report only the current lifecycle layer, user-capability
delta, blocker delta, one next action, one prohibited action, CAP, and exact
nonclaims. Source, generated, canonical integration, installed, selected,
live, naturally exercised, result consumed, and user accepted are distinct.

## Final acceptance

Support artifacts, validators, fixtures, mirrors, audits, and source commits
receive zero user-capability credit. Final acceptance requires all U1 through
U8, 10 consecutive natural closed-loop journeys across at least three task
classes, at least three restart or recovery cases, zero manual relays, zero
critical failures, and explicit same-surface user acceptance.

The current bound Maestro revision is a source-composed candidate. It is not
canonical integration, installation, selection, live firing, result
consumption, or user acceptance.
