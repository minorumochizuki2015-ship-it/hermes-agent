#!/usr/bin/env python3
"""Bounded UserPromptSubmit selector for the existing MK733N compiler."""
from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import os
import re
import stat
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
COMPILER_PATH = REPO / "scripts/ops/mk733j_context_compiler.py"
SOURCE_ROOT = REPO / "research/mk675/fable5_decision_os"
CORPUS_PATH = SOURCE_ROOT / "mk733j_n_policy_corpus.json"
WORKPACK_PATH = SOURCE_ROOT / "mk733j_gpt56_model_neutral_workpack.json"
BASELINE_PATH = SOURCE_ROOT / "mk733j_n_context_baseline.json"
EXPECTED_COMPILER_VERSION = "mk733j-n-context-compiler.v2"
MAX_INPUT_BYTES = 32_768
MAX_CONTEXT_BYTES = 4_096
FALLBACK_CONTEXT = (
    "MK733J_TASK_CONTEXT_UNAVAILABLE_NONAUTHORITATIVE. Optional task-aware "
    "context was withheld; ordinary supervised baseline remains available. "
    "This output does not allow or block an operation and grants no receipt, "
    "authority, runtime, installation, effectiveness, or acceptance claim."
)
SELECTED_MARKER = "MK733J_TASK_CONTEXT_SELECTED_NONAUTHORITATIVE"
USE_CANONICAL_COMPILER = object()
ARTIFACT_FIELDS = {
    "artifact_digest",
    "artifact_role",
    "artifact_type",
    "artifact_version",
    "baseline_bytes",
    "baseline_digest",
    "baseline_version",
    "blocks",
    "compiled",
    "compiled_bytes",
    "compiled_to_baseline_ratio",
    "compiler_request",
    "context_digest",
    "context_payload",
    "decision_score_measurement_status",
    "decision_score_regression_points",
    "irrelevant_policy_refs",
    "non_claims",
    "request_digest",
    "required_policy_recall",
    "source_binding",
    "status",
}
COMPILED_FIELDS = {
    "policy_ids",
    "brief_requirements",
    "non_claims",
    "workpack_digest",
    "stop_and_escalation_rules",
}
COMPILER_NON_CLAIMS = [
    "no_model_quality_measurement",
    "no_raw_prompt_or_transcript_retention",
]
FORBIDDEN_EVENT_KEYS = {
    "credential",
    "credentials",
    "hidden_reasoning",
    "raw_prompt",
    "policy_ids",
    "required_non_claims",
    "required_policy_ids",
    "secret",
    "secrets",
    "token",
    "tokens",
    "transcript",
    "skill_refs",
}
INJECTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bignore\s+(?:all\s+)?previous\s+instructions\b",
        r"\breveal\s+(?:the\s+)?(?:system|developer|hidden)\b",
        r"\bprint\s+(?:the\s+)?(?:system|developer|hidden)\b",
        r"\bsystem\s+prompt\b",
        r"\bdeveloper\s+message\b",
    )
)
CLASSIFIERS = (
    {
        "task_class": "decision_routing",
        "patterns": (
            re.compile(r"\bmodel\s+rout(?:e|es|ing)\b", re.IGNORECASE),
            re.compile(r"\b(?:candidate|option)\s+(?:comparison|selection)\b", re.IGNORECASE),
            re.compile(r"\b(?:blind\s+)?tournament\b", re.IGNORECASE),
        ),
        "policy_ids": ("mk733j_gpt56_model_neutral_decision_os_workpack",),
        "required_non_claims": ("no_blanket_model_parity", "no_runtime_readiness"),
        "brief_requirements": (
            "Qualify routes; bind real consumers; countercheck high-impact proposals; disclose comparison bias.",
        ),
        "skill_refs": ("skills/best-evaluate/SKILL.md",),
    },
    {
        "task_class": "runtime_lifecycle",
        "patterns": (
            re.compile(r"\b(?:hook|session)\s+lifecycle\b", re.IGNORECASE),
            re.compile(r"\b(?:plugin|skill)\s+(?:distribution|install|lifecycle)\b", re.IGNORECASE),
            re.compile(r"\bfresh[- ]session\s+(?:selection|firing|runtime)\b", re.IGNORECASE),
        ),
        "policy_ids": ("mk733g_decision_os_firing_surfaces",),
        "required_non_claims": ("no_permanent_runtime_firing",),
        "brief_requirements": (
            "Hooks are incomplete; launcher and CI remain separate support layers.",
        ),
        "skill_refs": ("skills/skill-lifecycle/SKILL.md",),
    },
)


def _hook_output(context: str) -> dict[str, dict[str, str]]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_EVENT_KEYS or normalized.startswith("raw_"):
                return True
            if _contains_forbidden_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _classify(event: Any) -> dict[str, Any] | None:
    if (
        not isinstance(event, dict)
        or event.get("hook_event_name") != "UserPromptSubmit"
        or _contains_forbidden_key(event)
    ):
        return None
    prompt = event.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > 16_384:
        return None
    if any(pattern.search(prompt) for pattern in INJECTION_PATTERNS):
        return None
    matches = [
        classifier
        for classifier in CLASSIFIERS
        if any(pattern.search(prompt) for pattern in classifier["patterns"])
    ]
    return matches[0] if len(matches) == 1 else None


def _regular_canonical_skill(ref: str) -> bool:
    if not re.fullmatch(r"skills/[a-z0-9][a-z0-9-]*/SKILL\.md", ref):
        return False
    path = REPO / ref
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and not path.is_symlink()
        and resolved.parent.parent == (REPO / "skills").resolve()
    )


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _trusted_regular_file(path: Path) -> Path:
    metadata = path.lstat()
    resolved = path.resolve(strict=True)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or REPO.resolve() not in resolved.parents
    ):
        raise ValueError("untrusted source file")
    return resolved


def _file_digest(path: Path) -> str:
    return hashlib.sha256(_trusted_regular_file(path).read_bytes()).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(_trusted_regular_file(path).read_text(encoding="utf-8"))


@contextmanager
def _suppress_helper_output() -> Any:
    """Suppress Python, native, and inherited child output during helper calls."""
    saved_stdout: int | None = None
    saved_stderr: int | None = None
    null_fd: int | None = None
    restore_error: BaseException | None = None
    try:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except BaseException:
                pass
        saved_stdout = os.dup(1)
        saved_stderr = os.dup(2)
        null_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null_fd, 1)
        os.dup2(null_fd, 2)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            yield
    finally:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except BaseException:
                pass
        for saved_fd, target_fd in ((saved_stdout, 1), (saved_stderr, 2)):
            if saved_fd is None:
                continue
            try:
                os.dup2(saved_fd, target_fd)
            except BaseException as exc:
                if restore_error is None:
                    restore_error = exc
            finally:
                try:
                    os.close(saved_fd)
                except OSError:
                    pass
        if null_fd is not None:
            try:
                os.close(null_fd)
            except OSError:
                pass
        if restore_error is not None:
            raise restore_error


def _current_source_binding() -> dict[str, str]:
    baseline = _load_json(BASELINE_PATH)
    return {
        "compiler_ref": "scripts/ops/mk733j_context_compiler.py",
        "compiler_version": EXPECTED_COMPILER_VERSION,
        "compiler_digest": _file_digest(COMPILER_PATH),
        "corpus_ref": "research/mk675/fable5_decision_os/mk733j_n_policy_corpus.json",
        "corpus_digest": _file_digest(CORPUS_PATH),
        "workpack_ref": "research/mk675/fable5_decision_os/mk733j_gpt56_model_neutral_workpack.json",
        "workpack_digest": _file_digest(WORKPACK_PATH),
        "baseline_ref": "research/mk675/fable5_decision_os/mk733j_n_context_baseline.json",
        "baseline_digest": _digest(baseline.get("baseline_payload")),
        "baseline_artifact_digest": _file_digest(BASELINE_PATH),
    }


def _independent_artifact_valid(
    classifier: dict[str, Any], artifact: Any, request: dict[str, list[str]]
) -> bool:
    try:
        if type(artifact) is not dict or set(artifact) != ARTIFACT_FIELDS:
            return False
        compiled = artifact.get("compiled")
        if type(compiled) is not dict or set(compiled) != COMPILED_FIELDS:
            return False
        source_binding = _current_source_binding()
        baseline = _load_json(BASELINE_PATH)
        baseline_payload = baseline.get("baseline_payload")
        baseline_digest = _digest(baseline_payload)
        if baseline.get("baseline_digest") != baseline_digest:
            return False
        expected_compiled = {
            "policy_ids": list(classifier["policy_ids"]),
            "brief_requirements": list(classifier["brief_requirements"]),
            "non_claims": list(classifier["required_non_claims"]),
            "workpack_digest": source_binding["workpack_digest"],
            "stop_and_escalation_rules": [
                "unknown_or_unqualified_identity_stops_or_escalates"
            ],
        }
        compiled_bytes = len(_canonical(compiled))
        baseline_bytes = len(_canonical(baseline_payload))
        expected_ratio = compiled_bytes / baseline_bytes if baseline_bytes else 1.0
        unsealed = dict(artifact)
        artifact_digest = unsealed.pop("artifact_digest", None)
        return (
            artifact.get("artifact_type") == "mk733j_n_context_artifact"
            and type(artifact.get("artifact_version")) is int
            and artifact.get("artifact_version") == 2
            and artifact.get("artifact_role") == "compiled"
            and artifact.get("status")
            == "CONTEXT_COMPILED_RATIO_MEASURED_QUALITY_UNMEASURED"
            and artifact.get("blocks") == []
            and artifact.get("compiler_request") == request
            and artifact.get("request_digest") == _digest(request)
            and artifact.get("source_binding") == source_binding
            and compiled == expected_compiled
            and artifact.get("context_payload") == compiled
            and artifact.get("context_digest") == _digest(compiled)
            and artifact_digest == _digest(unsealed)
            and artifact.get("baseline_version") == baseline.get("version")
            and artifact.get("baseline_digest") == baseline_digest
            and type(artifact.get("compiled_bytes")) is int
            and artifact.get("compiled_bytes") == compiled_bytes
            and type(artifact.get("baseline_bytes")) is int
            and artifact.get("baseline_bytes") == baseline_bytes
            and type(artifact.get("compiled_to_baseline_ratio")) is float
            and artifact.get("compiled_to_baseline_ratio") == expected_ratio
            and expected_ratio <= 0.5
            and type(artifact.get("required_policy_recall")) is float
            and artifact.get("required_policy_recall") == 1.0
            and type(artifact.get("irrelevant_policy_refs")) is int
            and artifact.get("irrelevant_policy_refs") == 0
            and artifact.get("decision_score_regression_points") is None
            and artifact.get("decision_score_measurement_status")
            == "not_measured_blocking"
            and artifact.get("non_claims") == COMPILER_NON_CLAIMS
        )
    except (OSError, RuntimeError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _load_canonical_compiler() -> Any | None:
    try:
        metadata = COMPILER_PATH.lstat()
        resolved = COMPILER_PATH.resolve(strict=True)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or COMPILER_PATH.is_symlink()
            or REPO.resolve() not in resolved.parents
        ):
            return None
        spec = importlib.util.spec_from_file_location(
            "mk733j_prompt_selector_compiler", resolved
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        with _suppress_helper_output():
            spec.loader.exec_module(module)
        return (
            module
            if callable(getattr(module, "compile_context", None))
            and callable(getattr(module, "valid_context_artifact", None))
            else None
        )
    except BaseException:
        return None


def _selected_context(classifier: dict[str, Any], artifact: Any) -> str | None:
    policy_ids = list(classifier["policy_ids"])
    required_non_claims = list(classifier["required_non_claims"])
    brief_requirements = list(classifier["brief_requirements"])
    skill_refs = list(classifier["skill_refs"])
    if not all(_regular_canonical_skill(ref) for ref in skill_refs):
        return None
    if (
        not isinstance(artifact, dict)
        or artifact.get("status")
        != "CONTEXT_COMPILED_RATIO_MEASURED_QUALITY_UNMEASURED"
        or artifact.get("blocks") != []
        or artifact.get("artifact_role") != "compiled"
        or artifact.get("compiler_request")
        != {
            "policy_ids": policy_ids,
            "required_policy_ids": policy_ids,
            "required_non_claims": required_non_claims,
        }
        or not isinstance(artifact.get("compiled_to_baseline_ratio"), (int, float))
        or artifact["compiled_to_baseline_ratio"] > 0.5
    ):
        return None
    compiled = artifact.get("compiled")
    if (
        not isinstance(compiled, dict)
        or set(compiled)
        != {
            "policy_ids",
            "brief_requirements",
            "non_claims",
            "workpack_digest",
            "stop_and_escalation_rules",
        }
        or compiled.get("policy_ids") != policy_ids
        or compiled.get("brief_requirements") != brief_requirements
        or compiled.get("non_claims") != required_non_claims
        or compiled.get("stop_and_escalation_rules")
        != ["unknown_or_unqualified_identity_stops_or_escalates"]
        or artifact.get("context_payload") != compiled
        or not isinstance(artifact.get("source_binding"), dict)
        or compiled.get("workpack_digest")
        != artifact["source_binding"].get("workpack_digest")
    ):
        return None
    context_digest = artifact.get("context_digest")
    if (
        not isinstance(context_digest, str)
        or not re.fullmatch(r"[a-f0-9]{64}", context_digest)
    ):
        return None
    context = "\n".join(
        (
            SELECTED_MARKER,
            f"task_class={classifier['task_class']}",
            f"policy_ids={','.join(policy_ids)}",
            f"required_non_claims={','.join(required_non_claims)}",
            f"skill_refs={','.join(skill_refs)}",
            f"brief_requirements={' | '.join(brief_requirements)}",
            f"compiled_context_digest=sha256:{context_digest}",
            "boundary=nonauthoritative context only; existing operation authority, "
            "PreToolUse, installation, runtime, and acceptance boundaries are unchanged.",
        )
    )
    return context if len(context.encode("utf-8")) <= MAX_CONTEXT_BYTES else None


def build_hook_output(
    event: Any, *, compiler: Any = USE_CANONICAL_COMPILER
) -> dict[str, dict[str, str]]:
    """Return one sanitized hook object; optional context failure is nonblocking."""
    try:
        classifier = _classify(event)
        if classifier is None:
            return _hook_output(FALLBACK_CONTEXT)
        resolved_compiler = (
            _load_canonical_compiler()
            if compiler is USE_CANONICAL_COMPILER
            else compiler
        )
        if resolved_compiler is None or not callable(
            getattr(resolved_compiler, "compile_context", None)
        ):
            return _hook_output(FALLBACK_CONTEXT)
        request = {
            "policy_ids": list(classifier["policy_ids"]),
            "required_policy_ids": list(classifier["policy_ids"]),
            "required_non_claims": list(classifier["required_non_claims"]),
        }
        with _suppress_helper_output():
            artifact = resolved_compiler.compile_context(request)
            artifact_valid = resolved_compiler.valid_context_artifact(
                artifact, "compiled"
            )
        if artifact_valid is not True or not _independent_artifact_valid(
            classifier, artifact, request
        ):
            return _hook_output(FALLBACK_CONTEXT)
        context = _selected_context(classifier, artifact)
        return _hook_output(context if context is not None else FALLBACK_CONTEXT)
    except BaseException:
        return _hook_output(FALLBACK_CONTEXT)


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        event = (
            json.loads(raw.decode("utf-8"))
            if raw and len(raw) <= MAX_INPUT_BYTES
            else None
        )
        payload = build_hook_output(event)
    except BaseException:
        payload = _hook_output(FALLBACK_CONTEXT)
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
