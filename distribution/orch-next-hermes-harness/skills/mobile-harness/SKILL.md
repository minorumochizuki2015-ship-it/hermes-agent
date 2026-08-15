---
name: mobile-harness
version: "0.1.0"
description: Validate bounded mobile service windows and lock fallback.
author: MOC and Hermes Agent
license: MIT
platforms: [macos]
metadata:
  hermes:
    category: orch-next
    tags: [orch-next, mobile, ios, evidence, bounded-operations]
    related_skills: [skill-authoring, skill-lifecycle]
---

# Mobile Harness Skill

This skill validates a small, optional mobile-harness contract for a normal
Hermes operation: it can explain why a physical device is live, authorize a
short Remote Ops-owned service window, or wait for one qualified unlock event.
It does not drive a device, operate Remote Ops, persist a lease, change
Auto-Lock, request credentials, install dependencies, or establish product,
runtime, or user acceptance.

## When to Use

Use when a task needs a bounded, auditable decision about an iOS physical
device, locked Mirroring, a Portal reachability observation, or a physical
unlock fallback. Use the pure helper at
`skills/orch-next/mobile-harness/scripts/mobile_harness_contract.py` for
sanitized decisions. Do not use this skill as a mobile SSH client, a
remote-ops runtime, or an acceptance substitute.

The upstream identities and local package versions are fixed inputs for this
source version:

- `droidrun/mobile-harness` at `ace2e483a954431f84c9004991f4704d4609d25f`.
- `droidrun/ios-portal` at `621e3e9bf680d3ff5e1294eef0ad5e4536dce0b3`.
- `mobilerun-core==1.5.0`, `mobilerun-core-local==0.6.0`, and
  `mobilerun-sdk==5.1.0`.

Never auto-pull, update, or install these inputs. There is no cloud/provider
key, global dependency, package manager, or network requirement. Device
automation is optional support only; it is never a Remote Ops product,
runtime, or acceptance dependency.

## Prerequisites

- Read the active repository rules, current worktree, branch, revision, dirty
  state, owner, and exact write boundary before changing source.
- Keep one writer for the worktree and use current repository source as truth.
- Use `terminal` only to invoke the pure helper or the repository-prescribed
  tests. The helper has no device calls, process commands, polling, hidden
  I/O, or credential path.
- The actual XCTest target is exactly `Droidrun Server`.
- Remote Ops owns the device-local, nonexportable, in-memory service-window
  lease and its UIKit consumer. This Hermes helper only validates sanitized
  contract and event decisions; it does not persist the live lease.
- Passcode, Face ID, Touch ID, Apple password, OTP, Trust, developer trust,
  and recovery are user-only transitions. Never request, read, store, inject,
  log, screenshot, OCR, or accessibility-inspect their secrets. After a
  credential screen, do not export raw UI, screenshots, trees, DOM, or form
  state.

## How to Run

The helper accepts one JSON object on stdin and emits one deterministic JSON
object on stdout. This small CLI is a convenience for a normal Hermes
consumer; it is not a device bridge.

```text
terminal: python skills/orch-next/mobile-harness/scripts/mobile_harness_contract.py
```

The available CLI observations are deliberately narrow. For the full pure
consumer API, import the helper in a test and call its functions directly:

- `validate_service_window(request)`
- `decide_operation(window, operation, context)`
- `revoke_service_window(window, cause)`
- `authority_envelope_contains(window, ...)`
- `build_waiting_checkpoint(...)`
- `evaluate_waiting_event(checkpoint, event)`
- `evaluate_host_wake(checkpoint, events)`
- `validate_portal_live(evidence)`
- `validate_foreground_binding(observation, ...)`
- `decide_portal_observe_launch_reobserve(...)`
- `project_learning_state(observation)`
- `decide_one_shot_control_transition(...)`
- `validate_iproxy_binding(...)`
- `select_private_ios_device(result, logical_identifier=...)`
- `get_devicectl_identifier(selection)` and `get_iproxy_udid(selection)`
- `ios_selection_decision(result, logical_identifier=...)`
- `classify_control_context(...)`
- `decide_observe_action_reobserve(...)`

Inputs are observations, not commands. Callers perform any authorized
Remote Ops action separately and then submit a new observation.

## Quick Reference

| Boundary | Required contract | Hermes claim |
| --- | --- | --- |
| Private iOS selection | `result.devices[].identifier` is the `devicectl` ID; `hardwareProperties.udid` is the only `iproxy` ID | Explicit consumer separation; IDs stay private and are never serialized |
| Portal live | XCTest Runner/process presence, exact target, and loopback HTTP `200 /device/date` | Sanitized live/not-live decision |
| `iproxy` | Literal loopback host and exact opaque authorized binding | Sanitized binding decision; no identifier receipt |
| Control mode | Unlocked physical control or locked Mirroring, never a blend | Explicit classification |
| Service window | `physical_unlocked_required`, `mirroring_locked_required`, or `either` | 15, 30, or 60 minutes; default 30 |
| Lease owner | Remote Ops/UIKit, nonexportable in memory | No Hermes lease persistence |
| Locked fallback | One safe checkpoint and one qualified transition event | Zero model/device usage while waiting |
| Acceptance | Separate product, runtime, device, and user evidence | No acceptance credit |

## Procedure

### 1. Establish evidence without trusting stale text

For private iOS selection, pass the current `devicectl` result to
`select_private_ios_device`. The logical `result.devices[].identifier` is
available only through `get_devicectl_identifier`; the hardware
`result.devices[].hardwareProperties.udid` is available only through
`get_iproxy_udid`. Never use the logical identifier as an `iproxy` fallback.
The selection receipt contains only `valid`, `decision`, and `retry_count`,
and the typed `IOS_DEVICE_SELECTION_MISSING`,
`IOS_DEVICE_SELECTION_MALFORMED`, `IOS_DEVICE_SELECTION_DUPLICATE`, or
`IOS_DEVICE_SELECTION_AMBIGUOUS` errors contain no input values and never
retry. Missing or malformed IDs, duplicate IDs, a multi-device result without
a logical selector, or a selector that does not map exactly one device fails
closed. Use `ios_selection_decision` or the CLI operation
`select_private_ios_device` when a serialized receipt is required.

For Portal, call `validate_portal_live` only from current observations showing
both XCTest Runner/process presence and a successful loopback HTTP request to
exactly `/device/date`. The runner target must be exactly `Droidrun Server`.
Stale Xcode console text, a remembered launch, a source declaration, or a
reachable-looking port alone is never live evidence.

Before any credential, trust, or UI claim, call `validate_foreground_binding`
with the exact expected bundle ID and app label. SpringBoard, an unknown app,
or any other foreground app returns the typed `foreground_app_mismatch`
decision with credential, trust/UI, and device-action claims withheld. An exact
binding plus an allowlisted marker may classify a protected screen; that still
does not authorize a user-only credential or trust transition. The helper
returns no raw foreground identity, marker, tree, screenshot, device ID, or
secret.

For `iproxy`, call `validate_iproxy_binding` with the hardware UDID returned by
`get_iproxy_udid` as the exact opaque binding and a literal loopback address
(`127.0.0.1` or `::1`). The authorized and proxy bindings must match. The
result contains no hardware identifier. Do not bind a public or LAN address,
infer hardware identity, or persist a receipt that can identify the device.

Keep these evidence layers separate: source, install, signing, reachable,
exercised, product behavior, and user acceptance. The
`project_learning_state` projection keeps `submitted`, `visible`, `consumed`,
`decision_changed`, `implementation_adopted`, `runtime_exercised`, and
`user_accepted` independent; it never infers a later state from an earlier
one. A passing source helper or reachable endpoint establishes none of the
later layers.

### 2. Classify physical and Mirroring control separately

Call `classify_control_context` before selecting an operation class. Unlocked
physical control is `physical`; locked Mirroring is `mirroring`; mixed states
are `unsupported`. A Mirroring observation never authorizes a physical-unlocked
operation, and a physical observation never proves locked Mirroring behavior.

### 3. Validate one primary service window

Build a request for exactly one task, session, account scope, and monotonic
generation. Select one of these operation classes:

- `physical_unlocked_required` for operations requiring the user-unlocked
  physical device.
- `mirroring_locked_required` for operations confined to locked Mirroring.
- `either` only when the operation contract explicitly permits either state.

`validate_service_window` accepts exactly 15, 30, or 60 minutes. Thirty is the
recommended default; sixty is allowed when explicitly selected. Indefinite
windows and silent renewal are denied. The decision returns an exact monotonic
deadline, an allowed-operation set, and deterministic counters. The allowed
set is contained by the safe operations `observe`, `install`, `launch`,
`current_operation`, and `continue`.

The window is valid only while the app is foreground and active, protected data
is available, the exact task/session/account/generation is current, and the
required transport is wired, paired, tunnel-ready, and DDI-ready. The request
must state that Remote Ops owns a nonexportable in-memory lease and that UIKit
is the consumer. The helper returns `lease_persisted: false`.

The window does not authorize credentials, paid providers, destructive work,
public deploy or release, App Store/TestFlight, account deletion, or acceptance.
`authority_envelope_contains` checks exact task/session/generation and operation
containment; it never expands the envelope or self-authorizes a protected
transition.

### 4. Revoke immediately and visibly

Revoke on `will_resign_active`, background, termination, manual lock, protected
data becoming unavailable, reboot, disconnect, trust loss, account sign-out,
task supersession, deadline, or manual stop. Every revoke decision sets
`idle_timer_disabled: false` immediately and cancels pending physical mutation.
There is no global Auto-Lock change. `isIdleTimerDisabled=true` is valid only
inside the still-valid window and must become false at revoke or expiry.

The user-facing surface shows only clear remaining time, allowed operations,
and Stop. Keep owner, stage, generation, and evidence codes out of display
copy. The actual countdown, Stop affordance, and approval remain in
Remote Ops/UIKit; Hermes supplies a sanitized decision only.

### 5. Sequence operations and suspend idle gaps

Inside a valid window, use this bounded sequence:

1. Observe current operation and state.
2. Install only when the observation says the source changed and installation
   is needed.
3. Launch only when launch is needed.
4. Observe the current operation and state again.
5. Permit at most one continuation whose cause changed.

The helper authorizes decisions; it does not perform these actions. Models
remain suspended during idle gaps. A manual lock, stale task/generation,
expired deadline, unavailable transport, or invalid account/session blocks the
next physical mutation. Never issue a blind chain, unchanged retry, or raw
process sweep. Cleanup is owner-checked and supported only; do not kill,
signal, invoke raw `ps`, or perform a manual process sweep.

For Portal control, use `decide_portal_observe_launch_reobserve` and require
the complete `observe -> launch -> reobserve` sequence. A live Portal check by
itself is never control acceptance. If the target launch invalidates the
Portal runner, the helper emits `portal_session_invalidated`; when the target
process remains and one authorized local Mirroring route is callable, it
permits exactly one `mirroring_failover_once`. Otherwise it returns
`WAITING_PHYSICAL_LOCK` or `WAITING_PHYSICAL_UNLOCK`, with zero model/tool/
device polling and no repeated Portal restart or `start_app` retry.

For a cause-changed control transition, use
`decide_one_shot_control_transition` with generic `physical` and `mirroring`
states. It permits at most one physical-to-Mirroring transition when the local
route is callable; a duplicate, unchanged cause, or unavailable route waits
with zero usage and polling disabled. This is a route decision, not proof of
runtime exercise or product effectiveness.

### 6. Use the locked fallback with zero usage

If the physical operation cannot proceed, publish one sanitized
`WAITING_PHYSICAL_UNLOCK` checkpoint containing only:

- task and session identity, generation, and the next-operation enum;
- exact source, package, and runtime generation;
- monotonic expiry and one-shot private generation/nonce;
- zero counters for `model_turns`, `totalTokens`, `toolCalls`,
  `device_polls`, `retries`, and `repeated_notifications`.

It must never contain a device identifier, passcode, token, account secret,
raw log, screenshot, UI/tree/DOM snapshot, or raw payload. Once published, the
model turn ends. A simulated 15/30/60-minute wait has zero model turns, token
usage, tool calls, device polls, retries, and repeated notifications. There is
no keep-awake behavior; host sleep means zero work.

Wait for a transition event, never poll. A single deterministic non-model guard
may resume the exact task once, and only when event source/consumer binding,
task/session/generation, expiry, transport, operation class, and unlocked
state all match. The helper returns a monotonic request counter and a resumed
checkpoint so duplicate or stale events cannot duplicate the operation.

Duplicate, stale, wrong-task, wrong-generation, locked, disconnected,
cancelled, expired, or superseded events produce zero model wake and zero
mutation. After success, cancel, expiry, supersession, disconnect
terminalization, or archive, remove the event subscription.

If there is no qualified event source or consumer binding, return the typed
`LOCK_STATE_EVENT_SOURCE_UNAVAILABLE`, never poll, and expose at most one
normal-surface resume affordance or wait for the next ordinary interaction.
Apple-side `protectedDataDidBecomeAvailable`,
`protectedDataWillBecomeUnavailable`, and app active/inactive notifications are
candidate native events only. They remain unqualified for a cross-surface
Hermes task wake until an actual consumer binding and runtime receipt exist.

### 7. Keep user-only transitions out of the contract

If a credential or trust screen appears, stop and tell the user what manual
action is needed through the normal surface. Never ask Hermes to read or inject
the value. Do not inspect or export the screen after entry. Revoke the service
window if the protected-data, trust, account, task, transport, or foreground
precondition is lost.

The prior v0.1.42 credential-effectiveness interpretation is withdrawn. The
existing `iproxy` reuse decision remains only a factual binding result; this
source change claims no device effectiveness, natural firing, or
observed-effective prevention.

## Pitfalls

- A stale console line is not a live Portal Runner and HTTP proof.
- Portal liveness alone is not control acceptance; require observe, launch,
  and reobserve.
- An exact foreground app binding is mandatory before credential, trust, or UI
  claims; SpringBoard or another app is a typed mismatch with zero device
  action.
- A source SHA, installed package, signature, reachable port, or helper PASS is
  not exercised behavior, product support, runtime readiness, or acceptance.
- A loopback proxy is not permission to expose a device or persist its ID.
- Locked Mirroring is not an unlocked physical device.
- A service window is not a credential, paid-provider, deploy, release,
  App Store/TestFlight, deletion, or acceptance authorization.
- A checkpoint is not a model prompt, log archive, screenshot, or device state
  snapshot.
- Native Apple notifications without a bound Hermes consumer are not a task
  wake source.
- Repeating an unchanged event or adding a polling loop violates the contract.
- Hermes does not silently renew, keep the host awake, kill processes, or
  substitute a nearby runtime or public route.

## Verification

Run the behavior tests at
`tests/hermes_cli/test_mobile_harness_skill_contract.py`. They exercise the
actual pure consumer functions and the JSON CLI without live network,
credentials, device calls, or process commands. Also run:

```text
terminal: python -m py_compile skills/orch-next/mobile-harness/scripts/mobile_harness_contract.py
terminal: scripts/run_tests.sh tests/hermes_cli/test_mobile_harness_skill_contract.py -q
```

The focused tests cover duration denial, valid windows, every revoke cause,
manual lock, envelope containment, safe zero-usage checkpoints, exactly-once
events, host wake, unavailable event sources, secret exclusion, physical versus
Mirroring classification, private iOS logical/hardware ID separation and
fail-closed mapping errors, Portal evidence, `iproxy` binding, and monotonic
observe/action/reobserve sequencing.

This source change claims only local source and pure-helper behavior. It does
not claim installed adoption, fresh skill selection, runtime exposure, device
reachability, signing, exercised mobile behavior, Remote Ops integration,
product support, effectiveness, or user acceptance. Product/support credit is
zero.

For lifecycle work, follow `skill-authoring` and `skill-lifecycle`: preserve
the source version and fixed identities, keep distribution and installed cache
separate, and obtain any protected authority result from the admitted authority
surface rather than recreating or executing Authority here.
