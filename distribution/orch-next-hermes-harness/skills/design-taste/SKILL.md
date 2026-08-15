---
name: design-taste
version: "1.0.0"
description: Use for new UI direction; not minor visual fixes.
author: ORCH-Next contributors and Hermes Agent
license: MIT
metadata:
  hermes:
    category: orch-next
    tags: [orch-next, operations, hermes-exclusive]
    ownership_manifest: "maestro-kernel:research/mk675/fable5_decision_os/mk737_p1a_skill_distribution_ownership.json"
    canonical_binding:
      contract: "design-taste-canonical-binding.v1"
      selection_namespace: "orch-next-hermes-harness:design-taste"
      collision_policy: "fail_closed"
      source_identity: "claude:design-taste"
      source_version: "0.4.0"
      source_skill_sha256: "c00e3e0af5c907b2016ae854afa31c35f7324d93e62f9e51e568850bd983bfaf"
      required_references:
        - path: "references/anti-generic-rules.md"
          sha256: "c6e11d852a86ca474a7ec4658bf01b6a6cdc22ab0a153387024e03d9145abe34"
        - path: "references/japanese-typography.md"
          sha256: "8a3d8e169d4641b71db380600febec9b5c81aeb488461dd1372c00182a0edf1b"
        - path: "references/reference-site-teardowns.md"
          sha256: "4273dea046f3804a020189023959656fa5094429b0c7b45de3b7d0d4389164e6"
        - path: "references/llmo-aio-evidence.md"
          sha256: "cea8642ab722d123e20d0ecf7b41ef13de942a2f1da5dfb2160a2cbe9e330d56"
---

# Design Taste Skill

Translate product identity and content into a non-generic visual direction and an implementation-ready critique.

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

- Before use, run
  `python scripts/verify_canonical_binding.py --selected-namespace orch-next-hermes-harness:design-taste`.
  It verifies the compact package's exact rich-reference closure. When the
  canonical Claude source is present, it also verifies that source at v0.4.0.
  Any observable digest, reference, or namespace ambiguity returns
  `canonical_binding_mismatch` with `launch=false`; do not call a similarly
  named legacy, unprefixed, or degraded skill.
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
2. Read `references/anti-generic-rules.md`; for Japanese surfaces also read
   `references/japanese-typography.md`. Read
   `references/reference-site-teardowns.md` when a measured precedent, copy
   voice, or information architecture decision is needed. Read
   `references/llmo-aio-evidence.md` only when discoverability is in scope.
   Keep the direction grounded in the actual product, locale, data, viewport,
   and normal-user loop.
3. Define the palette, typography, layout rhythm, signature element, real
   content/data mapping, state treatment, responsive behavior, and explicit
   anti-generic corrections before implementation.
4. Use native Hermes tools such as `read_file`, `search_files`, `terminal`,
   `patch`, `delegate_task`, `cronjob`, or `vision_analyze` only when
   they are available and necessary for this skill.
5. Keep source implementation, integration, installed adoption, fresh-session
   selection, runtime reachability, exercised behavior, user acceptance,
   effectiveness, and final completion separate.
6. Return the capability delta, blocker delta, exact owner and write set,
   checks run, rollback, next Hermes action, and precise non-claims.

## Failure behavior

- `canonical_binding_mismatch`: the selected namespace is ambiguous or the
  compact/canonical content or required-reference digest differs from the
  admitted v0.4.0 binding; return `launch=false` before design work.
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
