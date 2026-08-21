#!/usr/bin/env python3
"""Build and verify the ORCH-Next Hermes plugin source bundle.

The checked-in bundle is a deterministic mirror of ``skills/orch-next`` for
Codex and Claude plugin source channels.  Updates are staged and fully verified
before the existing bundle is replaced.  If the successor handoff or its final
verification fails, the prior bundle is restored.

This module deliberately does not install plugins, edit host configuration, or
provide an alternate executor.  Its only MCP process is the existing Hermes
stdio server at ``agent.transports.hermes_tools_mcp_server``.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import pwd
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from typing import Iterator, Sequence

_IMPORT_ROOT = Path(__file__).resolve().parents[1]
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

from agent.skill_materializer import (  # noqa: E402
    compiler_profile,
    materialize_skill,
)

PLUGIN_ID = "orch-next-hermes-harness"
PLUGIN_VERSION = "0.1.49"
PLUGIN_RELEASE_NOTE = (
    "0.1.49: Codex UserPromptSubmit carries the accepted bounded Maestro "
    "Decision OS prompt-context selector; installation and firing remain unclaimed."
)
MARKETPLACE_ID = "orch-next-hermes-local"
LEGACY_PLUGIN_ID = "orch-next-codex-harness"
SOURCE_SCHEMA = "orch-next-hermes-harness-distribution.v1"
MCP_MODULE = "agent.transports.hermes_tools_mcp_server"
HERMES_AGENT_RUNTIME_VERSION = "0.20.1"
HERMES_AGENT_UPSTREAM_TAG = "v2026.8.13"
HERMES_AGENT_UPSTREAM_COMMIT = "f80f453ae0679347e38abc917c7f94f717bf96c5"
HERMES_AGENT_LATEST_RELEASE_PATH = "repos/NousResearch/hermes-agent/releases/latest"
COMPACT_OPERATIONAL_PROFILE_ID = "orch-next-hermes-compact-operational-profile.v1"
COMPACT_OPERATIONAL_PROFILE_MODE = "metadata_index_then_selected_skill_on_demand"
OPERATIONAL_PROFILE_INDEX_PATH = "OPERATIONAL_PROFILE_INDEX.json"
OPERATIONAL_PROFILE_INDEX_SCHEMA = "orch-next-hermes-operational-profile-index.v1"
MAESTRO_AUTHORITY_BUNDLE_ID = "HERMES_MAESTRO_AUTHORITY_BUNDLE_V3"
MAESTRO_AUTHORITY_BUNDLE_VERSION = "hermes-maestro-authority-bundle.v3"
MAESTRO_AUTHORITY_BUNDLE_DIGEST = (
    "7d6bc36e50938f74ad2728ed3d87f272620086de7bfd928616c84bbdfd09412e"
)
MAESTRO_OWNERSHIP_MANIFEST = (
    "maestro-kernel:research/mk675/fable5_decision_os/"
    "mk737_p1a_skill_distribution_ownership.json"
)
OPERATIONAL_SOURCE_AUTHORITY = "hermes_agent_canonical_operational_skill_tree"
OPERATIONAL_SOURCE_ROOT = "skills/orch-next"
AUTHORITY_POLICY_SOURCE = "maestro_kernel_active_policy_and_routing"
LEGACY_MAESTRO_COMPATIBILITY = {
    "canonical_skill_root": "skills",
    "plugin_skill_root": "plugins/orch-next-codex-harness/skills",
    "classification": "legacy_non_authoritative_compatibility_material",
    "same_name_bytes_authoritative": False,
    "plugin_distribution_is_mirror": False,
    "zero_consumer_proof": False,
    "retirement_claimed": False,
    "status": "pending_zero_consumer_proof_and_retirement",
}
TERMINAL_AUTHORITY_CONTRACT_ID = "INC191_PRE_IDLE_SUCCESSOR_ADMISSION_V1"
TERMINAL_AUTHORITY_CONTRACT_VERSION = "1.1.0"
MAESTRO_AUTHORITY_SOURCE_REVISION = "af162d0576abf1c243afc53a6f2deaa669e0417f"
TERMINAL_AUTHORITY_SOURCE = "scripts/ops/mk_whole_goal_control.py"
TERMINAL_AUTHORITY_SOURCE_SHA256 = (
    "1183c28805e3a35033172505d9616ef247222d4e6cb5dd3425363c51b3d9615b"
)
TERMINAL_AUTHORITY_PROFILE = "skills/heartbeat-cmd-control-guard/OPERATIONAL_PROFILE.md"
TERMINAL_AUTHORITY_PROFILE_SHA256 = (
    "a57c57fc6cbe65c5657324ebbc737a370c7ef24ea6ae5cc2f0305ec94607c0be"
)
EXPECTED_SKILL_COUNT = 47
EXPECTED_SKILL_FILE_COUNT = 70
EXPECTED_SKILL_CLOSURE_DIGEST = (
    "c869e171d1cb15c6e5004642b9db3a51fa19033471cc824a65566269ab073329"
)
QUARANTINED_SKILLS = frozenset({"fable5-os-durable-user-value-goal"})
REQUIRED_ADMITTED_SKILLS = frozenset({"fable5-derived-advisory-synthesis"})
ALLOWED_BUNDLE_TOP_LEVEL = frozenset({
    ".claude-plugin",
    ".codex-plugin",
    ".mcp.json",
    OPERATIONAL_PROFILE_INDEX_PATH,
    "SOURCE_MANIFEST.json",
    "codex-hooks",
    "runtime",
    "skills",
})
ALLOWED_INSTALLED_RUNTIME_MARKERS = frozenset({".in_use"})
ORPHANED_INSTALLED_MARKER = ".orphaned_at"
RUNTIME_LOCATOR_SCHEMA = "orch-next-hermes-runtime-locator.v1"
RUNTIME_LOCATOR_MODE_PORTABLE = "manifest_relative"
RUNTIME_LOCATOR_MODE_INSTALLED = "installer_materialized"
RUNTIME_WRAPPER_PATH = "runtime/orch_next_hermes_mcp_launcher.py"
RUNTIME_BINDING_PATH = "runtime/RUNTIME_BINDING.json"
MAESTRO_PROMPT_CONTEXT_ROOT = "runtime/maestro_prompt_context"
MAESTRO_PROMPT_CONTEXT_INTAKE = "MAESTRO_SOURCE_INTAKE.json"
MAESTRO_PROMPT_CONTEXT_SCHEMA = "maestro-prompt-context-source-intake.v1"
MAESTRO_PROMPT_CONTEXT_SOURCE_COMMIT = (
    "568fe9ab0804e0b8b51a2e728f691e4a5edb9f26"
)
MAESTRO_PROMPT_CONTEXT_SOURCE_TREE = "31e6e9fb34e456f99b33e3c2ced0d0117be7e66e"
MAESTRO_PROMPT_CONTEXT_FILES = (
    ".codex/hooks/mk733j_prompt_task_selector.py",
    "scripts/ops/mk733j_context_compiler.py",
    "research/mk675/fable5_decision_os/mk733j_gpt56_model_neutral_workpack.json",
    "research/mk675/fable5_decision_os/mk733j_n_context_baseline.json",
    "research/mk675/fable5_decision_os/mk733j_n_decision_os_implementation.json",
    "research/mk675/fable5_decision_os/mk733j_n_policy_corpus.json",
    "skills/best-evaluate/SKILL.md",
    "skills/skill-lifecycle/SKILL.md",
)
MAESTRO_PROMPT_CONTEXT_DIGESTS = {
    ".codex/hooks/mk733j_prompt_task_selector.py": (
        "c4be69f08672acb9931cc21f03aac55260b98d88abd333160180d35886702af0"
    ),
    "scripts/ops/mk733j_context_compiler.py": (
        "d16b80f71f0ffd1b1c2850a0b7cd71ecd4c8b4472014e83966a39382ff6dc8e6"
    ),
    "research/mk675/fable5_decision_os/mk733j_gpt56_model_neutral_workpack.json": (
        "661468238587e89555417955ffd6c3b71a57b7aed72fcfaafaf40a5e8247193e"
    ),
    "research/mk675/fable5_decision_os/mk733j_n_context_baseline.json": (
        "9dc1af1964b1b204e9bef39c981e818a6eabedba51c8c16b0c330d5ba6ed461b"
    ),
    "research/mk675/fable5_decision_os/mk733j_n_decision_os_implementation.json": (
        "741335360a705e6a8ed4faf96381d6cc0cd74fef146835dfce7fb66ecbb77e39"
    ),
    "research/mk675/fable5_decision_os/mk733j_n_policy_corpus.json": (
        "51151c260ba1b93710036e3e95e94456b16d3d7418e1d37ed135906ee69979c4"
    ),
    "skills/best-evaluate/SKILL.md": (
        "7ffca0bfb7e602bedc1fb94747747b6bc0f052e8dd844b18f6d9b42230c28101"
    ),
    "skills/skill-lifecycle/SKILL.md": (
        "e511b125d2fe94bae0db30f4e1556d39736b170cecf6be31059188bb980ffe21"
    ),
}
CODEX_HOOKS_PATH = "codex-hooks/hooks.json"
RUNTIME_PORTABLE_SOURCE_ROOT = "../../.."
RUNTIME_PYTHON_PATH = ".venv/bin/python"
RUNTIME_LAUNCHER_PATH = "scripts/orch_next_hermes_mcp_launcher.py"
RUNTIME_SYSTEM_PYTHON = "/usr/bin/python3"
ORCH_OVERLAY_MINIMUM_REVISION = "8585d5d9de143750e85629000e62576a1e082169"
SDO_PRODUCER_MIRROR_ROOT = "runtime/sdo_producer"
SDO_PRODUCER_SOURCE_REVISION = "c25555b54315b8dc868d12b8699b500b9aab8094"
SDO_PRODUCER_SOURCE_TREE = "ba7e28fef29e9a28c93ff9226f260e74bc061e3c"
ROLLBACK_VERSION = "0.1.48"
ROLLBACK_IDENTITY = f"installed:{PLUGIN_ID}@{ROLLBACK_VERSION}"
PREDECESSOR_SOURCE_ONLY_VERSIONS = (
    "0.1.43",
    "0.1.44",
    "0.1.45",
    "0.1.46",
    "0.1.47",
)
SDO_PRODUCER_MIRROR_FILES = (
    "scripts/ops/issue_inc178_current_transition.py",
    "scripts/ops/mk_whole_goal_control.py",
    "scripts/ops/resolve_mk94_priority_action_queue.py",
    "research/mk675/fable5_derived/synthesis_records.json",
    "scripts/ops/mk733j_activation.py",
    "scripts/ops/mk733j_decision_os.py",
    "scripts/ops/mk733j_hook_contract_self_test.py",
    "scripts/ops/verify_mk733j_n_implementation.py",
    "scripts/ops/critical_thread_route.py",
    "scripts/ops/mk_decision_preflight.py",
    "scripts/ops/mk733j_qualification.py",
    "scripts/ops/mk733j_capability_bundles.py",
    "scripts/ops/mk733j_context_compiler.py",
    "scripts/ops/requirement_anchor_semantic.py",
    "scripts/ops/mk_adaptive_work_pace.py",
    "scripts/ops/mk_fable5_execution_authority.py",
    "scripts/ops/mk733j_schema_safety.py",
    "scripts/ops/verify_task_ledger_v1.py",
    "scripts/ops/mk747_fable5_cognitive_core.py",
)
SDO_PRODUCER_MIRROR_DIGESTS = {
    "scripts/ops/issue_inc178_current_transition.py": (
        "3e64f8ff5a86ed455311512cc4708ff12691ba7aa48616e0d22725bec31be91f"
    ),
    "scripts/ops/mk_whole_goal_control.py": (
        "1183c28805e3a35033172505d9616ef247222d4e6cb5dd3425363c51b3d9615b"
    ),
    "scripts/ops/resolve_mk94_priority_action_queue.py": (
        "dceb9fbdc2213328ecd5f2e6764507752af777d02b22921fc0cfc44e28316201"
    ),
    "research/mk675/fable5_derived/synthesis_records.json": (
        "a44c3e620f6abfdce8011f25a51f1b2114401a362f34a164fbe9e49ff7f0fc5c"
    ),
    "scripts/ops/mk733j_activation.py": (
        "d41908bb9fa5383af63b04fc8913395bcac0c287a96a293b328361e2d27baa70"
    ),
    "scripts/ops/mk733j_decision_os.py": (
        "cab8203aa0ded50090180802e11620c944d22c0a602325a936d1de744965209b"
    ),
    "scripts/ops/mk733j_hook_contract_self_test.py": (
        "c31ebce2459a9afbe7957f615ea57f4c0bb76e4ecffacffb2d5e3ca9435c570f"
    ),
    "scripts/ops/verify_mk733j_n_implementation.py": (
        "21ae47eb1d290757018ac1c44e6e53f9d6f88cc50edac2ba5f73bd39e83871ec"
    ),
    "scripts/ops/critical_thread_route.py": (
        "b36c23bac978c685c929f421c7d68aedf20d044917b6c6f6f3567b77a9440519"
    ),
    "scripts/ops/mk_decision_preflight.py": (
        "022e62cd08d4b1647483e9729149c78177ac661a884b96c7a3506adabacaa3c1"
    ),
    "scripts/ops/mk733j_qualification.py": (
        "5ea2453c1be243959be38f3ce0e0a4a423488f89685e7669c066653f073769e7"
    ),
    "scripts/ops/mk733j_capability_bundles.py": (
        "b4aa76823d3b531ef2d0dd043319ebb26e43f82c2c2fa649644b471cd31d1fbd"
    ),
    "scripts/ops/mk733j_context_compiler.py": (
        "d16b80f71f0ffd1b1c2850a0b7cd71ecd4c8b4472014e83966a39382ff6dc8e6"
    ),
    "scripts/ops/requirement_anchor_semantic.py": (
        "fc0692e21cd4cd739bb0482c00ea80e767921d5f5afeb740a8f9715420394b13"
    ),
    "scripts/ops/mk_adaptive_work_pace.py": (
        "429d3c9d2414ba611b695e28565db4fe1cc00aa488e2792def8c9104a899c40a"
    ),
    "scripts/ops/mk_fable5_execution_authority.py": (
        "754f13330e34e8f341d3b1856347b0dfe250178b59b1d5ecde8c8ff1fb8db697"
    ),
    "scripts/ops/mk733j_schema_safety.py": (
        "9b00438889ee2b24eef5e9a572433d29677b49555f5fb54adc18fe4b77e3a9b0"
    ),
    "scripts/ops/verify_task_ledger_v1.py": (
        "ed4f401acc8ee253e9a3bab98ee661ac1987384fdf7d488c2a34104ee674de49"
    ),
    "scripts/ops/mk747_fable5_cognitive_core.py": (
        "9cc031ec45e9aa74b83cddbf6c6dad39408a238832ab0384f48907cc48d40460"
    ),
}
MAESTRO_SKILL_SOURCE_REVISION = "c25555b54315b8dc868d12b8699b500b9aab8094"
MAESTRO_SKILL_SOURCE_TREE = "ba7e28fef29e9a28c93ff9226f260e74bc061e3c"
MAESTRO_SKILL_SOURCE_FILES = (
    "agent-dispatch/SKILL.md",
    "cmd-delegation-orchestration/SKILL.md",
    "codex-parallel-lanes/SKILL.md",
    "codex-parallel-lanes/scripts/setup_lane.sh",
    "heartbeat-cmd-control-guard/SKILL.md",
    "skill-select/SKILL.md",
)
MAESTRO_SKILL_SOURCE_DIGESTS = {
    "agent-dispatch/SKILL.md": (
        "1457df77ca0b2498fcacc267583216718e5d0b0f1ddbdd5143f7d2c4abe6a370"
    ),
    "cmd-delegation-orchestration/SKILL.md": (
        "0ec019f584fd887e1e142b0651ad747e9b02f11cdc3fca9dd82c209ae9559572"
    ),
    "codex-parallel-lanes/SKILL.md": (
        "e33169b6edac1806a56ade0672a28c5c374b546f038144eb99d525263cb787f4"
    ),
    "codex-parallel-lanes/scripts/setup_lane.sh": (
        "4c6d4551c8efeace3d5591c477df6927c78e40c66c7a24b13bf462e84a386255"
    ),
    "codex-parallel-lanes/scripts/setup_lane_failure_injection_test.sh": (
        "20cce2ab821ae0125e28c302a2eb150f1d6a48154be604da68ca6c475a04c647"
    ),
    "heartbeat-cmd-control-guard/SKILL.md": (
        "b6fdd7dd5eb027d8a9a34de4b6ce8b72f59d05a7ad0f9c5f653c8bf8e8f4f085"
    ),
    "skill-select/SKILL.md": (
        "df75af7c330cd00b0b449e410284dcc9e67eab61747e660dbad3b8dca62bbb36"
    ),
}
MAESTRO_SKILL_SOURCE_VALIDATION_FILES = {
    "scripts/ops/verify_critical_thread_route.py": (
        "0e664f7b526b2642099227cef1cedda518c682d3f41744a86951b838de062cd1"
    ),
    "scripts/ops/verify_inc178_whole_goal_work_selection.py": (
        "09b89c3b9a8a3d79cbeeb9a3db7957f786c36f6b35cfdca2e45c3e7d4e2d7667"
    ),
    "scripts/ops/verify_heartbeat_cmd_control_guard_skill.py": (
        "bfb2617e1f53bf986a9229629ceffee70d09f7bba1a9ab62acbdc6d17eaf85b1"
    ),
}
RUNTIME_ADMITTED_FILES = (
    RUNTIME_LAUNCHER_PATH,
    "agent/__init__.py",
    "agent/skill_materializer.py",
    "agent/skill_utils.py",
    "agent/jiter_preload.py",
    "agent/secret_sources/__init__.py",
    "agent/secret_sources/_cache.py",
    "agent/secret_sources/base.py",
    "agent/secret_sources/bitwarden.py",
    "agent/secret_sources/command.py",
    "agent/transports/hermes_orch_front_door.py",
    "agent/transports/hermes_tools_mcp_server.py",
    "hermes_cli/__init__.py",
    "hermes_cli/audit_firing_admission.py",
    "hermes_cli/env_loader.py",
    "hermes_cli/main.py",
    "hermes_cli/subcommands/dashboard.py",
    "hermes_cli/web_server.py",
    "hermes_constants.py",
    "hermes_state.py",
    "model_tools.py",
    "pyproject.toml",
    "scripts/__init__.py",
    "scripts/orch_next_hermes_plugin_adoption.py",
    "scripts/orch_next_hermes_serve_service.py",
    "scripts/orch_next_hermes_serve_service_launcher.sh",
    "scripts/orch_next_hermes_session_token_source.py",
    "tui_gateway/__init__.py",
    "tui_gateway/maestro_authority.py",
    "tui_gateway/maestro_authority_allowed_signers",
    "tui_gateway/maestro_plugin_adoption_authority.py",
    "tui_gateway/sdo_adapter.py",
    "tui_gateway/server.py",
    *(f"{SDO_PRODUCER_MIRROR_ROOT}/{path}" for path in SDO_PRODUCER_MIRROR_FILES),
    "tools/skills_tool.py",
    "skills/orch-next/heartbeat-cmd-control-guard/scripts/heartbeat_control.py",
)


class DistributionError(RuntimeError):
    """A source bundle violates the admitted distribution contract."""


def _maestro_skill_source_binding() -> dict:
    return {
        "source": "maestro-kernel",
        "revision": MAESTRO_SKILL_SOURCE_REVISION,
        "tree": MAESTRO_SKILL_SOURCE_TREE,
        "adaptation": "existing_hermes_operational_skill_tree",
        "files": [
            {
                "path": f"skills/{path}",
                "sha256": MAESTRO_SKILL_SOURCE_DIGESTS[path],
            }
            for path in MAESTRO_SKILL_SOURCE_FILES
        ],
        "validation_files": [
            {"path": path, "sha256": digest}
            for path, digest in MAESTRO_SKILL_SOURCE_VALIDATION_FILES.items()
        ],
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_source_skills() -> Path:
    return _repo_root() / "skills" / "orch-next"


def default_bundle_target() -> Path:
    return _repo_root() / "distribution" / PLUGIN_ID


def runtime_python() -> Path:
    return _repo_root() / ".venv" / "bin" / "python"


def runtime_launcher() -> Path:
    return _repo_root() / "scripts" / "orch_next_hermes_mcp_launcher.py"


def runtime_hermes_home() -> Path:
    """Return the fixed ORCH profile used by the persistent serve service."""

    return Path(str(pwd.getpwuid(os.getuid()).pw_dir)) / ".hermes" / "profiles" / "orch"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_file_entries(source_root: Path) -> list[dict[str, str]]:
    source_root = source_root.resolve(strict=True)
    entries: list[dict[str, str]] = []
    admitted_files = (RUNTIME_LAUNCHER_PATH, *RUNTIME_ADMITTED_FILES[1:])
    for relative in admitted_files:
        relative_path = _validate_relative_path(relative, label="admitted runtime file")
        path = source_root.joinpath(*relative_path.parts)
        if path.is_symlink() or not path.is_file():
            if relative == RUNTIME_LAUNCHER_PATH:
                raise DistributionError(
                    "portable Hermes runtime locator launcher unavailable"
                )
            raise DistributionError(f"admitted runtime file unavailable: {relative}")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(source_root):
            raise DistributionError(
                f"admitted runtime file escapes source root: {relative}"
            )
        entries.append({"path": relative, "sha256": _sha256_file(path)})
    return entries


def _runtime_files_digest(entries: Sequence[dict[str, str]]) -> str:
    stream = "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in entries)
    return hashlib.sha256(stream.encode("utf-8")).hexdigest()


def _sdo_producer_binding(source_root: Path) -> dict:
    mirror_root = source_root / SDO_PRODUCER_MIRROR_ROOT
    files: list[dict[str, str]] = []
    for relative in SDO_PRODUCER_MIRROR_FILES:
        path = mirror_root / relative
        if path.is_symlink() or not path.is_file():
            raise DistributionError(f"SDO producer mirror unavailable: {relative}")
        observed = _sha256_file(path)
        expected = SDO_PRODUCER_MIRROR_DIGESTS[relative]
        if observed != expected:
            raise DistributionError(f"SDO producer mirror drift: {relative}")
        files.append({"path": relative, "sha256": observed})
    return {
        "root": SDO_PRODUCER_MIRROR_ROOT,
        "source_revision": SDO_PRODUCER_SOURCE_REVISION,
        "source_tree": SDO_PRODUCER_SOURCE_TREE,
        "consumer_path": SDO_PRODUCER_MIRROR_FILES[0],
        "consumer_sha256": SDO_PRODUCER_MIRROR_DIGESTS[SDO_PRODUCER_MIRROR_FILES[0]],
        "control_path": SDO_PRODUCER_MIRROR_FILES[1],
        "control_sha256": SDO_PRODUCER_MIRROR_DIGESTS[SDO_PRODUCER_MIRROR_FILES[1]],
        "fable_records_path": SDO_PRODUCER_MIRROR_FILES[3],
        "fable_records_sha256": SDO_PRODUCER_MIRROR_DIGESTS[SDO_PRODUCER_MIRROR_FILES[3]],
        "files": files,
    }


def _runtime_python_identity(source_root: Path) -> dict[str, int | str]:
    """Bind the resolved interpreter bytes without persisting its host path."""

    lexical = source_root / RUNTIME_PYTHON_PATH
    try:
        resolved = lexical.resolve(strict=True)
        observed = resolved.stat()
    except OSError as exc:
        raise DistributionError(
            "portable Hermes runtime locator interpreter unavailable"
        ) from exc
    if (
        not stat.S_ISREG(observed.st_mode)
        or not os.access(resolved, os.X_OK)
        or observed.st_size <= 0
    ):
        raise DistributionError(
            "portable Hermes runtime locator interpreter unavailable"
        )
    return {
        "runtime_python_sha256": _sha256_file(resolved),
        "runtime_python_size": observed.st_size,
    }


def _runtime_binding(runtime_root: Path | None = None) -> dict:
    source_root = _repo_root().resolve(strict=True)
    entries = _runtime_file_entries(source_root)
    sdo_producer = _sdo_producer_binding(source_root)
    if runtime_root is None:
        mode = RUNTIME_LOCATOR_MODE_PORTABLE
        root_value = RUNTIME_PORTABLE_SOURCE_ROOT
    else:
        admitted_root = runtime_root.resolve(strict=True)
        if admitted_root != source_root:
            raise DistributionError("installer runtime root is not the admitted source")
        mode = RUNTIME_LOCATOR_MODE_INSTALLED
        root_value = str(admitted_root)
    return {
        "authority_bundle_digest": MAESTRO_AUTHORITY_BUNDLE_DIGEST,
        "authority_source": TERMINAL_AUTHORITY_SOURCE,
        "authority_source_revision": MAESTRO_AUTHORITY_SOURCE_REVISION,
        "authority_source_sha256": TERMINAL_AUTHORITY_SOURCE_SHA256,
        "minimum_source_revision": ORCH_OVERLAY_MINIMUM_REVISION,
        "mode": mode,
        "plugin_id": PLUGIN_ID,
        "plugin_version": PLUGIN_VERSION,
        "rollback_identity": ROLLBACK_IDENTITY,
        "runtime_files": entries,
        "runtime_files_digest": _runtime_files_digest(entries),
        "runtime_launcher": RUNTIME_LAUNCHER_PATH,
        "runtime_python": RUNTIME_PYTHON_PATH,
        "sdo_producer": sdo_producer,
        **_runtime_python_identity(source_root),
        "schema": RUNTIME_LOCATOR_SCHEMA,
        "skill_closure_digest": EXPECTED_SKILL_CLOSURE_DIGEST,
        "source_root": root_value,
        "upstream_commit": HERMES_AGENT_UPSTREAM_COMMIT,
        "upstream_tag": HERMES_AGENT_UPSTREAM_TAG,
        "upstream_version": HERMES_AGENT_RUNTIME_VERSION,
    }


def verify_runtime_baseline() -> dict[str, str]:
    """Fail closed unless this source descends from the pinned stable release."""
    root = _repo_root()
    try:
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        pyproject_version = pyproject["project"]["version"]
        init_text = (root / "hermes_cli" / "__init__.py").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise DistributionError("Hermes Agent runtime version unavailable") from exc
    expected_literal = f'__version__ = "{HERMES_AGENT_RUNTIME_VERSION}"'
    if (
        pyproject_version != HERMES_AGENT_RUNTIME_VERSION
        or expected_literal not in init_text
    ):
        raise DistributionError("Hermes Agent runtime version drift")
    check = subprocess.run(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            HERMES_AGENT_UPSTREAM_COMMIT,
            "HEAD",
        ],
        cwd=root,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
    if check.returncode != 0:
        raise DistributionError("Hermes Agent upstream baseline drift")
    tag_commit = subprocess.run(
        ["git", "rev-parse", f"refs/tags/{HERMES_AGENT_UPSTREAM_TAG}^{{commit}}"],
        cwd=root,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=5,
    )
    if (
        tag_commit.returncode != 0
        or tag_commit.stdout.strip() != HERMES_AGENT_UPSTREAM_COMMIT
    ):
        raise DistributionError("Hermes Agent upstream tag binding drift")
    return {
        "version": HERMES_AGENT_RUNTIME_VERSION,
        "tag": HERMES_AGENT_UPSTREAM_TAG,
        "commit": HERMES_AGENT_UPSTREAM_COMMIT,
    }


def _validated_latest_stable_release(value: object) -> dict[str, str]:
    """Project GitHub's latest-release response onto public provenance only."""
    if type(value) is not dict:
        raise DistributionError("official latest stable release unavailable")
    tag = value.get("tag_name")
    name = value.get("name")
    notes = value.get("body")
    published_at = value.get("published_at")
    if (
        value.get("draft") is not False
        or value.get("prerelease") is not False
        or type(tag) is not str
        or not tag.startswith("v20")
        or len(tag) > 64
        or type(name) is not str
        or not name.strip()
        or type(notes) is not str
        or not notes.strip()
        or type(published_at) is not str
        or not published_at.strip()
    ):
        raise DistributionError("official latest stable release metadata invalid")
    return {
        "tag": tag,
        "name": name.strip(),
        "published_at": published_at.strip(),
        "release_notes": "present",
    }


def fetch_latest_stable_release() -> dict[str, str]:
    """Read the official latest stable release once; no polling or mutation."""
    gh = shutil.which("gh")
    if gh is None:
        raise DistributionError("official latest stable release unavailable")
    try:
        completed = subprocess.run(
            [
                gh,
                "api",
                "--method",
                "GET",
                HERMES_AGENT_LATEST_RELEASE_PATH,
            ],
            cwd=_repo_root(),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except Exception as exc:
        raise DistributionError("official latest stable release unavailable") from exc
    if completed.returncode != 0:
        raise DistributionError("official latest stable release unavailable")
    payload = completed.stdout
    if len(payload) > 1024 * 1024:
        raise DistributionError("official latest stable release metadata too large")
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DistributionError(
            "official latest stable release metadata invalid"
        ) from exc
    return _validated_latest_stable_release(decoded)


def verify_latest_stable_release(
    release: object | None = None,
) -> dict[str, str]:
    """Block adoption when the immutable candidate is no longer latest stable."""
    checked = (
        fetch_latest_stable_release()
        if release is None
        else _validated_latest_stable_release(release)
    )
    if checked["tag"] != HERMES_AGENT_UPSTREAM_TAG:
        raise DistributionError(
            "newer official stable release requires bounded forward port: "
            f"candidate={HERMES_AGENT_UPSTREAM_TAG}, latest={checked['tag']}"
        )
    baseline = verify_runtime_baseline()
    return {
        **checked,
        "candidate_commit": baseline["commit"],
        "candidate_version": baseline["version"],
        "status": "latest_stable_verified",
    }


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _validate_relative_path(value: str, *, label: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise DistributionError(f"{label} contains a path escape: {value!r}")
    if "\\" in value:
        raise DistributionError(f"{label} must use POSIX separators: {value!r}")
    return path


def _maestro_prompt_context_rows(root: Path) -> list[dict[str, int | str]]:
    expected_paths = set(MAESTRO_PROMPT_CONTEXT_FILES)
    expected_closure = expected_paths | {MAESTRO_PROMPT_CONTEXT_INTAKE}
    _reject_symlinks(root, label="Maestro prompt context")
    observed_paths = {
        path.relative_to(root).as_posix()
        for path in _regular_files(root, label="Maestro prompt context")
    }
    if observed_paths != expected_closure:
        raise DistributionError(
            "Maestro prompt context closure mismatch: "
            f"extra={sorted(observed_paths - expected_closure)}, "
            f"missing={sorted(expected_closure - observed_paths)}"
        )
    rows: list[dict[str, int | str]] = []
    for relative in MAESTRO_PROMPT_CONTEXT_FILES:
        path = root / relative
        mode = stat.S_IMODE(path.lstat().st_mode)
        if mode != 0o644:
            raise DistributionError(f"Maestro prompt context mode drift: {relative}")
        digest = _sha256_file(path)
        if digest != MAESTRO_PROMPT_CONTEXT_DIGESTS[relative]:
            raise DistributionError(f"Maestro prompt context digest drift: {relative}")
        rows.append({
            "mode": "100644",
            "path": relative,
            "sha256": digest,
            "size": path.stat().st_size,
        })
    intake_path = root / MAESTRO_PROMPT_CONTEXT_INTAKE
    if stat.S_IMODE(intake_path.lstat().st_mode) != 0o644:
        raise DistributionError(
            f"Maestro prompt context mode drift: {MAESTRO_PROMPT_CONTEXT_INTAKE}"
        )
    return rows


def _maestro_prompt_context_closure_digest(
    rows: Sequence[dict[str, int | str]],
) -> str:
    stream = "".join(
        f"{row['sha256']}  {row['mode']}  {row['path']}\n" for row in rows
    )
    return hashlib.sha256(stream.encode("utf-8")).hexdigest()


def _maestro_prompt_context_intake(
    rows: Sequence[dict[str, int | str]],
) -> dict:
    return {
        "closure_digest": _maestro_prompt_context_closure_digest(rows),
        "file_count": len(rows),
        "files": list(rows),
        "private_runtime_dependencies": True,
        "schema": MAESTRO_PROMPT_CONTEXT_SCHEMA,
        "source_commit": MAESTRO_PROMPT_CONTEXT_SOURCE_COMMIT,
        "source_read": "exact_git_object_bytes",
        "source_repository": "maestro-kernel",
        "source_tree": MAESTRO_PROMPT_CONTEXT_SOURCE_TREE,
        "top_level_plugin_skill_claims": False,
    }


def _maestro_prompt_context_binding(root: Path) -> dict:
    rows = _maestro_prompt_context_rows(root)
    intake_path = root / MAESTRO_PROMPT_CONTEXT_INTAKE
    observed_intake = _read_json(intake_path, label="Maestro prompt context intake")
    expected_intake = _maestro_prompt_context_intake(rows)
    if observed_intake != expected_intake:
        raise DistributionError("Maestro prompt context intake drift")
    return {
        "closure_digest": expected_intake["closure_digest"],
        "file_count": expected_intake["file_count"],
        "files": list(rows),
        "hook": {
            "event": "UserPromptSubmit",
            "manifest": CODEX_HOOKS_PATH,
            "registered_channels": ["codex"],
        },
        "intake": f"{MAESTRO_PROMPT_CONTEXT_ROOT}/{MAESTRO_PROMPT_CONTEXT_INTAKE}",
        "intake_sha256": _sha256_file(intake_path),
        "root": MAESTRO_PROMPT_CONTEXT_ROOT,
        "source_commit": MAESTRO_PROMPT_CONTEXT_SOURCE_COMMIT,
        "source_tree": MAESTRO_PROMPT_CONTEXT_SOURCE_TREE,
    }


def _trusted_git(
    source_repo: Path,
    arguments: Sequence[str],
    *,
    text: bool = False,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/usr/bin/git", "-C", str(source_repo), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=text,
        env={
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
        },
    )


def _read_maestro_git_object(source_repo: Path, relative: str) -> tuple[bytes, str]:
    source_repo = source_repo.resolve(strict=True)
    tree = _trusted_git(
        source_repo,
        [
            "ls-tree",
            MAESTRO_PROMPT_CONTEXT_SOURCE_COMMIT,
            "--",
            relative,
        ],
        text=True,
    )
    if tree.returncode != 0 or not tree.stdout.strip():
        raise DistributionError(f"accepted Maestro source unavailable: {relative}")
    mode = tree.stdout.split(maxsplit=1)[0]
    if mode != "100644":
        raise DistributionError(f"accepted Maestro source mode drift: {relative}")
    blob = _trusted_git(
        source_repo,
        [
            "cat-file",
            "blob",
            f"{MAESTRO_PROMPT_CONTEXT_SOURCE_COMMIT}:{relative}",
        ],
    )
    if blob.returncode != 0:
        raise DistributionError(f"accepted Maestro source unavailable: {relative}")
    digest = hashlib.sha256(blob.stdout).hexdigest()
    if digest != MAESTRO_PROMPT_CONTEXT_DIGESTS[relative]:
        raise DistributionError(f"accepted Maestro source digest drift: {relative}")
    return blob.stdout, mode


def _materialize_maestro_prompt_context(
    destination: Path, source_repo: Path | None
) -> None:
    if source_repo is None:
        source = default_bundle_target() / MAESTRO_PROMPT_CONTEXT_ROOT
        _maestro_prompt_context_binding(source)
        shutil.copytree(source, destination, copy_function=shutil.copy2)
        return
    source_repo = source_repo.resolve(strict=True)
    observed_tree = _trusted_git(
        source_repo,
        [
            "rev-parse",
            f"{MAESTRO_PROMPT_CONTEXT_SOURCE_COMMIT}^{{tree}}",
        ],
        text=True,
    )
    if (
        observed_tree.returncode != 0
        or observed_tree.stdout.strip() != MAESTRO_PROMPT_CONTEXT_SOURCE_TREE
    ):
        raise DistributionError("accepted Maestro source tree drift")
    for relative in MAESTRO_PROMPT_CONTEXT_FILES:
        content, _mode = _read_maestro_git_object(source_repo, relative)
        _write_bytes(destination / relative, content)
    rows = [
        {
            "mode": "100644",
            "path": relative,
            "sha256": _sha256_file(destination / relative),
            "size": (destination / relative).stat().st_size,
        }
        for relative in MAESTRO_PROMPT_CONTEXT_FILES
    ]
    _write_bytes(
        destination / MAESTRO_PROMPT_CONTEXT_INTAKE,
        _json_bytes(_maestro_prompt_context_intake(rows)),
    )
    _maestro_prompt_context_binding(destination)


def _reject_symlinks(root: Path, *, label: str) -> None:
    if root.is_symlink():
        raise DistributionError(f"{label} root must not be a symlink: {root}")
    if not root.is_dir():
        raise DistributionError(f"{label} root is not a directory: {root}")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise DistributionError(f"{label} contains a symlink: {path}")


def _regular_files(root: Path, *, label: str) -> list[Path]:
    _reject_symlinks(root, label=label)
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise DistributionError(f"{label} contains a non-regular file: {path}")
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            # Match the canonical Hermes skill-closure contract: interpreter
            # caches are runtime residue, never distributable source content.
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _skill_entries(skills_root: Path) -> list[dict[str, str]]:
    return [
        {
            "path": path.relative_to(skills_root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in _regular_files(skills_root, label="skill closure")
    ]


def _skill_closure_digest(entries: Sequence[dict[str, str]]) -> str:
    stream = "".join(
        f"{entry['sha256']}  skills/orch-next/{entry['path']}\n" for entry in entries
    )
    return hashlib.sha256(stream.encode("utf-8")).hexdigest()


def _skill_names(skills_root: Path) -> frozenset[str]:
    return frozenset(
        child.name
        for child in skills_root.iterdir()
        if child.is_dir() and not child.is_symlink() and child.name != "__pycache__"
    )


def _canonical_existing_directory(path: Path, *, label: str) -> Path:
    """Return one lexical, real directory without following an alias root."""

    lexical = path.expanduser().absolute()
    if lexical.is_symlink():
        raise DistributionError(f"{label} root must not be a symlink: {lexical}")
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise DistributionError(f"{label} root is unavailable: {lexical}") from exc
    if resolved != lexical or not resolved.is_dir():
        raise DistributionError(f"{label} root is not canonical: {lexical}")
    return lexical


def _canonical_new_directory(path: Path, *, label: str) -> Path:
    """Validate a possibly absent directory through its canonical parent."""

    lexical = path.expanduser().absolute()
    if lexical.exists() or lexical.is_symlink():
        return _canonical_existing_directory(lexical, label=label)
    parent = _canonical_existing_directory(lexical.parent, label=f"{label} parent")
    if lexical.parent != parent:
        raise DistributionError(f"{label} root is not canonical: {lexical}")
    return lexical


def _all_file_tree_identity(root: Path) -> tuple[list[dict[str, str]], str]:
    """Bind every regular file in a skill tree; caches are not exempt here."""

    _reject_symlinks(root, label="quarantine skill tree")
    entries: list[dict[str, str]] = []
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        if path.is_dir():
            continue
        if not stat.S_ISREG(path.lstat().st_mode):
            raise DistributionError(
                f"quarantine skill tree contains a non-regular file: {path}"
            )
        entries.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256_file(path),
        })
    stream = "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in entries)
    return entries, hashlib.sha256(stream.encode("utf-8")).hexdigest()


def inventory_unprefixed_skill_collisions(
    canonical_skills: Path,
    channel_skill_roots: dict[str, Path],
) -> dict:
    """Inventory active unprefixed names that collide with Hermes skills."""

    canonical_root = _canonical_existing_directory(
        canonical_skills, label="canonical skill"
    )
    _reject_symlinks(canonical_root, label="canonical skill")
    canonical_names = _skill_names(canonical_root)
    channels: dict[str, dict] = {}
    total = 0
    for channel in sorted(channel_skill_roots):
        root = _canonical_existing_directory(
            channel_skill_roots[channel], label=f"{channel} active skill"
        )
        collision_paths = sorted(
            (root / name for name in canonical_names if (root / name).exists()),
            key=lambda path: path.name,
        )
        collisions: list[str] = []
        for collision in collision_paths:
            if collision.is_symlink() or not collision.is_dir():
                raise DistributionError(
                    f"{channel} colliding skill is not a real directory: {collision.name}"
                )
            _reject_symlinks(collision, label=f"{channel} colliding skill")
            collisions.append(collision.name)
        total += len(collisions)
        channels[channel] = {
            "active_root": str(root),
            "collision_count": len(collisions),
            "collisions": collisions,
        }
    return {
        "canonical_skill_count": len(canonical_names),
        "channels": channels,
        "status": "active_collisions" if total else "no_active_collisions",
        "total_active_collision_count": total,
    }


def _path_is_bound_to_root(value: object, roots: Sequence[Path]) -> bool:
    if type(value) is not str or not value or not Path(value).is_absolute():
        return False
    lexical = Path(value).absolute()
    lexical_match = any(
        lexical == root or lexical.is_relative_to(root) for root in roots
    )
    if not lexical.exists() and not lexical.is_symlink():
        return lexical_match
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise DistributionError("observed process path alias is unresolved") from exc
    resolved_match = any(
        resolved == root or resolved.is_relative_to(root) for root in roots
    )
    return lexical_match or resolved_match


def classify_live_consumer_processes(
    processes: Sequence[dict],
    *,
    legacy_roots: Sequence[Path],
) -> dict:
    """Classify only processes provably bound to a legacy plugin checkout.

    A normal ``codex exec`` or ``codex -p`` worker is not evidence of a legacy
    consumer.  The legacy classification requires an executable, parent
    launcher, or argv path under an admitted legacy plugin root.  Raw argv is
    never returned.
    """

    roots = tuple(
        _canonical_existing_directory(root, label="legacy plugin")
        for root in legacy_roots
    )
    active: list[dict[str, str]] = []
    codex_workers = 0
    for process in processes:
        if type(process) is not dict:
            raise DistributionError("process classification row must be an object")
        argv = process.get("argv")
        if type(argv) is not list or any(type(item) is not str for item in argv):
            raise DistributionError("process argv must be a string list")
        executable = process.get("executable")
        parent_launcher = process.get("parent_launcher")
        if type(executable) is not str or type(parent_launcher) is not str:
            raise DistributionError("process executable and parent launcher required")
        executable_name = Path(executable).name
        if executable_name == "codex" and (
            (len(argv) > 1 and argv[1] == "exec") or "-p" in argv
        ):
            codex_workers += 1
        binding: str | None = None
        if _path_is_bound_to_root(executable, roots):
            binding = "executable"
        elif _path_is_bound_to_root(parent_launcher, roots):
            binding = "parent_launcher"
        elif any(_path_is_bound_to_root(value, roots) for value in argv):
            binding = "argv"
        if binding is not None:
            active.append({
                "binding": binding,
                "consumer": "legacy_direct_dispatch",
            })
    return {
        "active_legacy_consumer_count": len(active),
        "active_legacy_consumers": active,
        "observed_codex_worker_count": codex_workers,
        "status": ("active_legacy_consumer" if active else "no_live_legacy_consumer"),
    }


def classify_legacy_plugin_caches(
    *,
    channel: str,
    legacy_cache_root: Path,
    registry_observation: object = None,
    active_install_paths: Sequence[Path],
) -> dict:
    """Bind legacy cache classification to registry state, path, and bytes."""

    expected_sources = {
        "claude": "claude_installed_plugins",
        "codex": "codex_config",
    }
    if channel not in expected_sources or type(registry_observation) is not dict:
        raise DistributionError("legacy plugin registry observation is invalid")
    expected_observation = {
        "identity": LEGACY_PLUGIN_ID,
        "source": expected_sources[channel],
        "state": registry_observation.get("state"),
    }
    if registry_observation != expected_observation or registry_observation.get(
        "state"
    ) not in {"enabled", "disabled", "absent"}:
        raise DistributionError("legacy plugin registry observation is invalid")
    registry_state = registry_observation["state"]
    if registry_state != "enabled" and active_install_paths:
        raise DistributionError(
            "inactive registry observation has active install paths"
        )
    root = _canonical_existing_directory(
        legacy_cache_root, label=f"{channel} legacy plugin cache"
    )
    referenced: set[Path] = set()
    for path in active_install_paths:
        active = _canonical_existing_directory(
            path, label=f"{channel} legacy active install"
        )
        if active.parent != root:
            raise DistributionError("legacy active install is outside its cache root")
        referenced.add(active)
    caches: list[dict] = []
    for candidate in sorted(root.iterdir(), key=lambda item: item.name):
        if candidate.is_symlink():
            raise DistributionError("legacy plugin cache contains a symlink")
        if not candidate.is_dir():
            continue
        candidate = _canonical_existing_directory(
            candidate, label=f"{channel} legacy cache version"
        )
        files, digest = _all_file_tree_identity(candidate)
        registry_referenced = candidate in referenced
        state = (
            "active"
            if registry_referenced or (registry_state == "enabled" and not referenced)
            else "rollback_only"
        )
        caches.append({
            "cache_path": str(candidate),
            "content_digest": digest,
            "file_count": len(files),
            "registry_referenced": registry_referenced,
            "state": state,
            "version": candidate.name,
        })
    missing = sorted(
        str(path) for path in referenced - {Path(row["cache_path"]) for row in caches}
    )
    if missing:
        raise DistributionError("legacy registry references an unclassified cache")
    active_count = sum(row["state"] == "active" for row in caches)
    rollback_count = sum(row["state"] == "rollback_only" for row in caches)
    return {
        "active_cache_count": active_count,
        "caches": caches,
        "channel": channel,
        "identity": LEGACY_PLUGIN_ID,
        "registry_observation": dict(registry_observation),
        "rollback_only_cache_count": rollback_count,
    }


def plan_claude_unprefixed_quarantine(
    canonical_skills: Path,
    active_root: Path,
    quarantine_root: Path,
    rollback_record: Path,
) -> dict:
    """Plan a reversible same-filesystem move of Claude skill collisions."""

    canonical = _canonical_existing_directory(canonical_skills, label="canonical skill")
    active = _canonical_existing_directory(active_root, label="Claude active skill")
    quarantine = _canonical_new_directory(quarantine_root, label="skill quarantine")
    record = rollback_record.expanduser().absolute()
    if record.parent != quarantine or record.name != "ROLLBACK_MAP.json":
        raise DistributionError("rollback record must be the exact quarantine map")
    if record.exists() or record.is_symlink():
        raise DistributionError("rollback record destination already exists")
    if active.stat().st_dev != quarantine.parent.stat().st_dev:
        raise DistributionError("skill quarantine must be on the same filesystem")
    inventory = inventory_unprefixed_skill_collisions(canonical, {"claude": active})
    entries: list[dict[str, object]] = []
    for skill in inventory["channels"]["claude"]["collisions"]:
        source = active / skill
        destination = quarantine / skill
        if destination.exists() or destination.is_symlink():
            raise DistributionError(
                f"skill quarantine destination already exists: {skill}"
            )
        files, digest = _all_file_tree_identity(source)
        entries.append({
            "destination": str(destination),
            "file_count": len(files),
            "skill": skill,
            "source": str(source),
            "tree_digest": digest,
        })
    return {
        "active_root": str(active),
        "canonical_root": str(canonical),
        "collision_count": len(entries),
        "entries": entries,
        "operation": "claude_unprefixed_skill_quarantine",
        "quarantine_root": str(quarantine),
        "rollback_record": str(record),
    }


def _validated_quarantine_plan(
    plan: object,
) -> tuple[Path, Path, Path, Path, list[dict]]:
    if type(plan) is not dict or plan.get("operation") != (
        "claude_unprefixed_skill_quarantine"
    ):
        raise DistributionError("Claude skill quarantine plan is invalid")
    try:
        canonical = _canonical_existing_directory(
            Path(plan["canonical_root"]), label="canonical skill"
        )
        active = _canonical_existing_directory(
            Path(plan["active_root"]), label="Claude active skill"
        )
        quarantine = _canonical_new_directory(
            Path(plan["quarantine_root"]), label="skill quarantine"
        )
        record = Path(plan["rollback_record"]).expanduser().absolute()
        entries = plan["entries"]
    except (KeyError, TypeError) as exc:
        raise DistributionError("Claude skill quarantine plan is invalid") from exc
    if (
        record.parent != quarantine
        or record.name != "ROLLBACK_MAP.json"
        or record.is_symlink()
        or type(entries) is not list
        or plan.get("collision_count") != len(entries)
    ):
        raise DistributionError("Claude skill quarantine plan is invalid")
    canonical_names = _skill_names(canonical)
    observed_names: list[str] = []
    for entry in entries:
        if type(entry) is not dict or set(entry) != {
            "destination",
            "file_count",
            "skill",
            "source",
            "tree_digest",
        }:
            raise DistributionError("Claude skill quarantine plan entry is invalid")
        skill = entry["skill"]
        digest = entry["tree_digest"]
        file_count = entry["file_count"]
        source_value = entry["source"]
        destination_value = entry["destination"]
        if (
            type(skill) is not str
            or skill not in canonical_names
            or "/" in skill
            or "\\" in skill
            or type(source_value) is not str
            or Path(source_value).absolute() != active / skill
            or type(destination_value) is not str
            or Path(destination_value).absolute() != quarantine / skill
            or type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or type(file_count) is not int
            or file_count < 0
        ):
            raise DistributionError("Claude skill quarantine plan entry is invalid")
        observed_names.append(skill)
    if observed_names != sorted(set(observed_names)):
        raise DistributionError("Claude skill quarantine plan entry order is invalid")
    filesystem_root = quarantine if quarantine.exists() else quarantine.parent
    if active.stat().st_dev != filesystem_root.stat().st_dev:
        raise DistributionError("skill quarantine must be on the same filesystem")
    return canonical, active, quarantine, record, entries


def _quarantine_entry_state(entry: dict) -> str:
    source = Path(entry["source"])
    destination = Path(entry["destination"])
    source_present = source.exists() or source.is_symlink()
    destination_present = destination.exists() or destination.is_symlink()
    if source_present == destination_present:
        raise DistributionError(
            f"quarantine entry state is ambiguous: {entry['skill']}"
        )
    observed = source if source_present else destination
    files, digest = _all_file_tree_identity(observed)
    if digest != entry["tree_digest"] or len(files) != entry["file_count"]:
        raise DistributionError(f"quarantine entry digest drift: {entry['skill']}")
    return "source_only" if source_present else "destination_only"


def _fsync_directory(path: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        os.fsync(descriptor)
    except OSError as exc:
        raise DistributionError(
            "quarantine directory could not be synchronized"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_rollback_map(path: Path, plan: dict) -> None:
    temporary = path.parent / ".ROLLBACK_MAP.stage"
    descriptor = -1
    created = False
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        created = True
        content = _json_bytes(plan)
        written = 0
        while written < len(content):
            count = os.write(descriptor, content[written:])
            if count <= 0:
                raise OSError("rollback record write made no progress")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if path.exists() or path.is_symlink():
            raise DistributionError("rollback record destination already exists")
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise DistributionError(
            "rollback record could not be written atomically"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if created and temporary.exists():
            temporary.unlink()


def execute_claude_unprefixed_quarantine(plan: dict) -> dict:
    """Execute a previously computed quarantine plan without deleting bytes."""

    _, _, quarantine, _, _ = _validated_quarantine_plan(plan)
    with _target_lock(quarantine):
        canonical, active, quarantine, record, entries = _validated_quarantine_plan(
            plan
        )
        if not record.exists():
            refreshed = plan_claude_unprefixed_quarantine(
                canonical,
                active,
                quarantine,
                record,
            )
            if refreshed != plan:
                raise DistributionError("Claude skill quarantine plan drift")
        if not quarantine.exists():
            quarantine.mkdir(mode=0o700)
        if active.stat().st_dev != quarantine.stat().st_dev:
            raise DistributionError("skill quarantine must be on the same filesystem")
        if record.exists():
            observed_record = _read_json(
                record, label="skill quarantine rollback record"
            )
            if observed_record != plan:
                raise DistributionError("Claude skill quarantine rollback map drift")
        else:
            initial_states = [_quarantine_entry_state(entry) for entry in entries]
            if any(state != "source_only" for state in initial_states):
                raise DistributionError(
                    "destination-only quarantine entry is missing its rollback map"
                )
            _write_rollback_map(record, plan)
        states = [_quarantine_entry_state(entry) for entry in entries]
        moved = 0
        already_moved = 0
        for entry, state in zip(entries, states, strict=True):
            if state == "destination_only":
                already_moved += 1
                continue
            _atomic_replace(Path(entry["source"]), Path(entry["destination"]))
            _fsync_directory(active)
            _fsync_directory(quarantine)
            if _quarantine_entry_state(entry) != "destination_only":
                raise DistributionError("quarantine entry move did not become durable")
            moved += 1
    return {
        "already_moved_count": already_moved,
        "collision_count": len(entries),
        "moved_count": moved,
        "rollback_record": str(record),
        "status": "quarantined",
    }


def rollback_claude_unprefixed_quarantine(
    rollback_record: Path,
    *,
    active_root: Path,
    quarantine_root: Path,
) -> dict:
    """Restore a digest-bound quarantine without overwriting active skills."""

    active = _canonical_existing_directory(active_root, label="Claude active skill")
    quarantine = _canonical_existing_directory(
        quarantine_root, label="skill quarantine"
    )
    with _target_lock(quarantine):
        return _rollback_claude_unprefixed_quarantine_locked(
            rollback_record,
            active=active,
            quarantine=quarantine,
        )


def _rollback_claude_unprefixed_quarantine_locked(
    rollback_record: Path,
    *,
    active: Path,
    quarantine: Path,
) -> dict:
    record_path = rollback_record.expanduser().absolute()
    if record_path.parent != quarantine or record_path.is_symlink():
        raise DistributionError("rollback record is outside the quarantine root")
    record = _read_json(record_path, label="skill quarantine rollback record")
    if type(record) is not dict:
        raise DistributionError("skill quarantine rollback record is invalid")
    _, record_active, record_quarantine, admitted_record, entries = (
        _validated_quarantine_plan(record)
    )
    if (
        admitted_record != record_path
        or record_active != active
        or record_quarantine != quarantine
    ):
        raise DistributionError("skill quarantine rollback root drift")
    states = [_quarantine_entry_state(entry) for entry in entries]
    restored = 0
    already_restored = 0
    for entry, state in zip(reversed(entries), reversed(states), strict=True):
        if state == "source_only":
            already_restored += 1
            continue
        _atomic_replace(Path(entry["destination"]), Path(entry["source"]))
        _fsync_directory(quarantine)
        _fsync_directory(active)
        if _quarantine_entry_state(entry) != "source_only":
            raise DistributionError("quarantine entry rollback did not become durable")
        restored += 1
    return {
        "already_restored_count": already_restored,
        "restored_count": restored,
        "rollback_record": str(record_path),
        "status": "rolled_back",
    }


def verify_zero_legacy_consumers(
    *,
    inventory: dict,
    plugin_classifications: Sequence[dict],
    process_classification: dict,
    quarantine_records: Sequence[dict],
    quarantined_skill_names: Sequence[str],
    installed_identity_records: Sequence[dict],
) -> dict:
    """Fail closed unless every active legacy execution seam is retired."""

    collisions = inventory.get("total_active_collision_count")
    if type(collisions) is not int or collisions < 0:
        raise DistributionError("active collision inventory is invalid")
    if collisions:
        raise DistributionError("active unprefixed skill collision remains")
    rollback_only = 0
    observed_channels: set[str] = set()
    expected_registry_sources = {
        "claude": "claude_installed_plugins",
        "codex": "codex_config",
    }
    for classification in plugin_classifications:
        if (
            type(classification) is not dict
            or classification.get("identity") != LEGACY_PLUGIN_ID
            or classification.get("channel") not in expected_registry_sources
        ):
            raise DistributionError("legacy plugin classification is invalid")
        channel = classification["channel"]
        if channel in observed_channels:
            raise DistributionError("duplicate legacy plugin channel classification")
        observed_channels.add(channel)
        observation = classification.get("registry_observation")
        if (
            type(observation) is not dict
            or observation
            != {
                "identity": LEGACY_PLUGIN_ID,
                "source": expected_registry_sources[channel],
                "state": observation.get("state"),
            }
            or observation.get("state") not in {"enabled", "disabled", "absent"}
        ):
            raise DistributionError("legacy plugin registry observation is invalid")
        if observation["state"] == "enabled":
            raise DistributionError("legacy plugin remains enabled")
        caches = classification.get("caches")
        if type(caches) is not list:
            raise DistributionError("legacy plugin cache classification is invalid")
        active_count = 0
        rollback_count = 0
        for cache in caches:
            if type(cache) is not dict:
                raise DistributionError("legacy plugin cache classification is invalid")
            digest = cache.get("content_digest")
            cache_path = cache.get("cache_path")
            file_count = cache.get("file_count")
            referenced = cache.get("registry_referenced")
            state = cache.get("state")
            if (
                type(digest) is not str
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or type(cache_path) is not str
                or not Path(cache_path).is_absolute()
                or type(file_count) is not int
                or file_count < 0
                or type(referenced) is not bool
                or state not in {"active", "rollback_only"}
                or (referenced and state != "active")
            ):
                raise DistributionError("legacy plugin cache classification is invalid")
            active_count += state == "active"
            rollback_count += state == "rollback_only"
        if active_count != classification.get(
            "active_cache_count"
        ) or rollback_count != (classification.get("rollback_only_cache_count")):
            raise DistributionError("legacy plugin cache classification count drift")
        if active_count:
            raise DistributionError("legacy plugin active cache remains")
        rollback_only += rollback_count
    if observed_channels != {"claude", "codex"}:
        raise DistributionError("legacy plugin channel classification incomplete")
    active_processes = process_classification.get("active_legacy_consumer_count")
    if type(active_processes) is not int or active_processes < 0:
        raise DistributionError("live consumer classification is invalid")
    if active_processes:
        raise DistributionError("live legacy direct dispatch remains")
    recorded_skills: set[str] = set()
    for record in quarantine_records:
        if type(record) is not dict or record.get("operation") != (
            "claude_unprefixed_skill_quarantine"
        ):
            continue
        entries = record.get("entries")
        if type(entries) is not list:
            continue
        for entry in entries:
            if type(entry) is not dict:
                continue
            skill = entry.get("skill")
            digest = entry.get("tree_digest")
            source = entry.get("source")
            destination = entry.get("destination")
            file_count = entry.get("file_count")
            if (
                type(skill) is str
                and type(digest) is str
                and len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest)
                and type(source) is str
                and Path(source).is_absolute()
                and type(destination) is str
                and Path(destination).is_absolute()
                and type(file_count) is int
                and file_count >= 0
            ):
                recorded_skills.add(skill)
    missing_records = sorted(set(quarantined_skill_names) - recorded_skills)
    if missing_records:
        raise DistributionError(
            "quarantined skill missing rollback record: " + ", ".join(missing_records)
        )
    for identity in installed_identity_records:
        if type(identity) is not dict or not identity.get("active"):
            continue
        if identity.get("content_digest") != identity.get("expected_content_digest"):
            raise DistributionError("same-semver installed content drift")
    return {
        "active_collision_count": collisions,
        "active_legacy_consumer_count": active_processes,
        "legacy_plugin_enabled": False,
        "rollback_only_cache_count": rollback_only,
        "status": "zero_legacy_consumers_verified",
    }


def _compact_operational_profile(entries: Sequence[dict[str, str]]) -> dict:
    """Bind Hermes-owned skills to separate Maestro policy inputs."""

    profile = {
        "authority_bundle": {
            "digest": MAESTRO_AUTHORITY_BUNDLE_DIGEST,
            "identity": MAESTRO_AUTHORITY_BUNDLE_ID,
            "version": MAESTRO_AUTHORITY_BUNDLE_VERSION,
        },
        "authority_policy_reference": MAESTRO_OWNERSHIP_MANIFEST,
        "authority_policy_source": AUTHORITY_POLICY_SOURCE,
        "content": {
            "digest": _skill_closure_digest(entries),
            "recursive_file_count": len(entries),
            "source_root": OPERATIONAL_SOURCE_ROOT,
        },
        "full_skill_closure_injected_into_runtime_prompt": False,
        "identity": COMPACT_OPERATIONAL_PROFILE_ID,
        "legacy_maestro_compatibility": dict(LEGACY_MAESTRO_COMPATIBILITY),
        "operational_source_authority": OPERATIONAL_SOURCE_AUTHORITY,
        "operational_source_binding": {
            "binding_kind": "immutable_SOURCE_MANIFEST",
            "binding_required_at": "source_verification",
            "binding_present": True,
            "manifest_path": "SOURCE_MANIFEST.json",
            "self_content_binding": {
                "algorithm": "sha256",
                "scope": "canonical_SOURCE_MANIFEST_without_self_digest",
            },
        },
        "prompt_materialization": COMPACT_OPERATIONAL_PROFILE_MODE,
        "source_root": OPERATIONAL_SOURCE_ROOT,
        "runtime_baseline": {
            "commit": HERMES_AGENT_UPSTREAM_COMMIT,
            "tag": HERMES_AGENT_UPSTREAM_TAG,
            "version": HERMES_AGENT_RUNTIME_VERSION,
        },
        "terminal_authority": {
            "contract_id": TERMINAL_AUTHORITY_CONTRACT_ID,
            "contract_version": TERMINAL_AUTHORITY_CONTRACT_VERSION,
            "profile": TERMINAL_AUTHORITY_PROFILE,
            "profile_sha256": TERMINAL_AUTHORITY_PROFILE_SHA256,
            "source": TERMINAL_AUTHORITY_SOURCE,
            "source_revision": MAESTRO_AUTHORITY_SOURCE_REVISION,
            "source_sha256": TERMINAL_AUTHORITY_SOURCE_SHA256,
        },
    }
    profile["profile_digest"] = hashlib.sha256(
        json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return profile


def _skill_trigger(name: str) -> tuple[str, str | None]:
    """Map native skill triggers without making conditionals mandatory."""

    if name == "skill-select":
        return "common_preflight", "multiple_candidates"
    if name == "best-evaluate":
        return "unresolved_comparison", "unresolved_option_tradeoff"
    if name == "orch-skill-ecosystem-improvement":
        return "repeated_miss_nonfire", "repeated_skill_miss_or_nonfire"
    return "task_specific", None


def _topology_neutral_execution_contract() -> dict:
    return {
        "authority": {
            "bounded_disjoint_luna_implementation_may_coexist": True,
            "protected_security_owner": "sol",
        },
        "q0_single_repo_is_calibration_only": True,
        "q1": {
            "fixed_objective_and_authority_boundary": True,
            "integration_acceptance_required": True,
            "integration_owner_required": True,
            "owner_transfer_requires_new_owner_epoch": True,
            "per_repository_acceptance_required": True,
            "per_repository_bindings": [
                "repository_id",
                "base_revision",
                "worktree",
                "write_set",
            ],
            "rollback_required": True,
            "timeout_required": True,
            "transport_switch_requires_new_attempt_and_owner_epoch": True,
        },
        "repo_count_is_selector": False,
        "topology": "topology_neutral",
    }


def _operational_profile_index(
    skills_root: Path,
    entries: Sequence[dict[str, str]],
) -> dict:
    """Compile the deterministic plugin-path identity for every skill."""

    closure_digest = _skill_closure_digest(entries)
    manifest_digest = hashlib.sha256(
        _json_bytes(_codex_plugin_manifest())
    ).hexdigest()
    source_manifest_identity = f"{SOURCE_SCHEMA}:{PLUGIN_ID}@{PLUGIN_VERSION}"
    plugin_binding_base = {
        "package_identity": PLUGIN_ID,
        "package_version": PLUGIN_VERSION,
        "manifest_digest": manifest_digest,
        "content_digest": closure_digest,
    }
    profiles: list[dict] = []
    for name in sorted(_skill_names(skills_root)):
        trigger_mode, source_trigger = _skill_trigger(name)
        qualified = f"{PLUGIN_ID}:{name}"
        materialized = materialize_skill(
            skills_root / name / "SKILL.md",
            qualified_skill_id=qualified,
            trigger_mode=trigger_mode,
            source_trigger=source_trigger,
            source_binding={
                "source_kind": "plugin_distribution",
                "source_manifest_identity": source_manifest_identity,
                # A checked-in artifact cannot contain the commit that contains
                # itself. Runtime attempts add the observed immutable HEAD;
                # the source artifact binds exact manifest and content digests.
                "source_head": None,
                "path": f"skills/{name}/SKILL.md",
            },
            mode="plugin_namespaced_resolve",
            plugin_binding={
                **plugin_binding_base,
                "namespaced_skill_id": qualified,
            },
        )
        receipt = materialized.receipt
        profiles.append({
            "qualified_skill_id": receipt["qualified_skill_id"],
            "trigger_mode": receipt["trigger_mode"],
            "source_trigger": receipt["source_trigger"],
            "source_binding": receipt["source_binding"],
            "required_references": receipt["required_references"],
            "materialization_binding": receipt["materialization_binding"],
            "plugin_binding": receipt["plugin_binding"],
        })
    compiler = compiler_profile()
    index = {
        "schema": OPERATIONAL_PROFILE_INDEX_SCHEMA,
        "identity": COMPACT_OPERATIONAL_PROFILE_ID,
        "compiler": {
            "identity": compiler["identity"],
            "version": compiler["version"],
            "digest": compiler["digest"],
            "profile_digest": compiler["profile_digest"],
        },
        "package": {
            "marketplace_id": MARKETPLACE_ID,
            "plugin_id": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "source_manifest_schema": SOURCE_SCHEMA,
            "plugin_manifest_digest": manifest_digest,
            "skill_closure_digest": closure_digest,
        },
        "maestro_skill_source": _maestro_skill_source_binding(),
        "execution_contract": _topology_neutral_execution_contract(),
        "skills": profiles,
    }
    index["index_digest"] = hashlib.sha256(_json_bytes(index)).hexdigest()
    return index


def verify_source_skills(skills_root: Path) -> list[dict[str, str]]:
    skills_root = skills_root.absolute()
    entries = _skill_entries(skills_root)
    names = _skill_names(skills_root)
    quarantined = sorted(names & QUARANTINED_SKILLS)
    if quarantined:
        raise DistributionError(
            "quarantined skill included in operational closure: "
            + ", ".join(quarantined)
        )
    missing_admitted = sorted(REQUIRED_ADMITTED_SKILLS - names)
    if missing_admitted:
        raise DistributionError(
            "required admitted skill missing: " + ", ".join(missing_admitted)
        )
    if len(names) != EXPECTED_SKILL_COUNT:
        raise DistributionError(
            f"skill count mismatch: expected {EXPECTED_SKILL_COUNT}, observed {len(names)}"
        )
    if len(entries) != EXPECTED_SKILL_FILE_COUNT:
        raise DistributionError(
            "skill file count mismatch: expected "
            f"{EXPECTED_SKILL_FILE_COUNT}, observed {len(entries)}"
        )
    for name in sorted(names):
        if not (skills_root / name / "SKILL.md").is_file():
            raise DistributionError(f"skill missing SKILL.md: {name}")
    observed_digest = _skill_closure_digest(entries)
    if observed_digest != EXPECTED_SKILL_CLOSURE_DIGEST:
        raise DistributionError(
            "skill closure drift: expected "
            f"{EXPECTED_SKILL_CLOSURE_DIGEST}, observed {observed_digest}"
        )
    return entries


def _mcp_manifest() -> dict:
    command = f"./{RUNTIME_WRAPPER_PATH}"
    _validate_codex_mcp_command(command)
    return {
        "mcpServers": {
            PLUGIN_ID: {
                "args": [],
                "command": command,
                "cwd": ".",
                "env": {
                    "HERMES_HOME": str(runtime_hermes_home()),
                    "HERMES_QUIET": "1",
                    "HERMES_REDACT_SECRETS": "true",
                },
                "type": "stdio",
            }
        }
    }


def _validate_codex_mcp_command(command: object) -> None:
    """Apply the current Codex Agent Plugins stdio command contract."""

    if type(command) is not str or not command or any(
        character.isspace() for character in command
    ):
        raise DistributionError(
            "Codex Agent Plugins stdio command must be bare or contained"
        )
    if command.startswith("./"):
        relative = PurePosixPath(command[2:])
        if not relative.parts or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            raise DistributionError(
                "Codex Agent Plugins stdio command must be bare or contained"
            )
        return
    if "/" not in command and "\\" not in command and command not in {".", ".."}:
        return
    raise DistributionError(
        "Codex Agent Plugins stdio command must be bare or contained"
    )


def _codex_plugin_manifest() -> dict:
    return {
        "author": {
            "name": "MOC",
            "url": "https://github.com/NousResearch/hermes-agent",
        },
        "description": (
            "Exclusive ORCH-Next operational skills and persistent session tools "
            "served by Hermes Agent."
        ),
        "homepage": "https://github.com/NousResearch/hermes-agent",
        "hooks": f"./{CODEX_HOOKS_PATH}",
        "interface": {
            "capabilities": ["Interactive", "Read", "Write"],
            "category": "Developer Tools",
            "defaultPrompt": [
                "Continue this ORCH-Next project through the persistent operational harness."
            ],
            "developerName": "MOC",
            "displayName": "ORCH-Next Hermes Harness",
            "longDescription": (
                "Routes ORCH-Next operational session lifecycle and worker workflows "
                "through Hermes while Maestro remains the authority kernel."
            ),
            "shortDescription": "One persistent ORCH-Next operational front door",
        },
        "keywords": ["orch-next", "hermes", "sessions", "workers", "audit"],
        "license": "MIT",
        "mcpServers": "./.mcp.json",
        "name": PLUGIN_ID,
        "repository": "https://github.com/NousResearch/hermes-agent",
        "skills": "./skills/",
        "version": PLUGIN_VERSION,
    }


def _codex_hooks_manifest() -> dict:
    return {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "command": (
                                "/usr/bin/python3 \"$PLUGIN_ROOT/runtime/"
                                "maestro_prompt_context/.codex/hooks/"
                                "mk733j_prompt_task_selector.py\""
                            ),
                            "statusMessage": "Loading Decision OS context",
                            "timeout": 5,
                            "type": "command",
                        }
                    ]
                }
            ]
        }
    }


def _claude_plugin_manifest() -> dict:
    return {
        "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
        "author": {
            "name": "MOC",
            "url": "https://github.com/NousResearch/hermes-agent",
        },
        "description": (
            "Exclusive ORCH-Next operational skills and persistent session tools "
            "served by Hermes Agent."
        ),
        "homepage": "https://github.com/NousResearch/hermes-agent",
        "license": "MIT",
        "mcpServers": "./.mcp.json",
        "name": PLUGIN_ID,
        "repository": "https://github.com/NousResearch/hermes-agent",
        "skills": "./skills/",
        "version": PLUGIN_VERSION,
    }


def _codex_marketplace_manifest() -> dict:
    return {
        "interface": {"displayName": "ORCH-Next Hermes Local"},
        "name": MARKETPLACE_ID,
        "plugins": [
            {
                "category": "Coding",
                "name": PLUGIN_ID,
                "policy": {
                    "authentication": "ON_INSTALL",
                    "installation": "AVAILABLE",
                },
                "source": {
                    "path": f"./distribution/{PLUGIN_ID}",
                    "source": "local",
                },
            }
        ],
    }


def _claude_marketplace_manifest() -> dict:
    return {
        "description": (
            "Local qualification marketplace for the exclusive ORCH-Next Hermes "
            "operational harness."
        ),
        "name": MARKETPLACE_ID,
        "owner": {"name": "ORCH-Next Hermes"},
        "plugins": [
            {
                "description": (
                    "Exclusive ORCH-Next operational skills and persistent "
                    "session tools served by Hermes Agent."
                ),
                "name": PLUGIN_ID,
                "source": f"./distribution/{PLUGIN_ID}",
                "version": PLUGIN_VERSION,
            }
        ],
    }


def _source_manifest(
    entries: Sequence[dict[str, str]],
    runtime_binding: dict,
    profile_index: dict,
    prompt_context_binding: dict,
) -> dict:
    manifest = {
        "channels": {
            "claude": {
                "manifest": ".claude-plugin/plugin.json",
                "mcp": ".mcp.json",
                "skills": "skills",
            },
            "codex": {
                "manifest": ".codex-plugin/plugin.json",
                "mcp": ".mcp.json",
                "skills": "skills",
            },
        },
        "claims": {
            "cross_project_acceptance": False,
            "exclusive_default": False,
            "installed_adoption": False,
            "persistent_runtime_acceptance": False,
            "source_bundle": True,
        },
        "upstream_base": HERMES_AGENT_UPSTREAM_COMMIT,
        "authority_policy_reference": MAESTRO_OWNERSHIP_MANIFEST,
        "authority_policy_source": AUTHORITY_POLICY_SOURCE,
        "execution_owner": "hermes",
        "identity": PLUGIN_ID,
        "hermes_agent_runtime": {
            "upstream_commit": HERMES_AGENT_UPSTREAM_COMMIT,
            "upstream_tag": HERMES_AGENT_UPSTREAM_TAG,
            "version": HERMES_AGENT_RUNTIME_VERSION,
        },
        "heartbeat_terminal_authority": {
            "contract_id": TERMINAL_AUTHORITY_CONTRACT_ID,
            "contract_version": TERMINAL_AUTHORITY_CONTRACT_VERSION,
            "profile": TERMINAL_AUTHORITY_PROFILE,
            "profile_sha256": TERMINAL_AUTHORITY_PROFILE_SHA256,
            "source": TERMINAL_AUTHORITY_SOURCE,
            "source_revision": MAESTRO_AUTHORITY_SOURCE_REVISION,
            "source_sha256": TERMINAL_AUTHORITY_SOURCE_SHA256,
        },
        "mcp": {
            "codex_execution_fallback_allowed": False,
            "locator": {
                "binding": RUNTIME_BINDING_PATH,
                "binding_sha256": hashlib.sha256(
                    _json_bytes(runtime_binding)
                ).hexdigest(),
                "mode": runtime_binding["mode"],
                "rollback_identity": runtime_binding["rollback_identity"],
            },
            "maestro_execution_fallback_allowed": False,
            "module": MCP_MODULE,
            "launcher": RUNTIME_WRAPPER_PATH,
            "python": RUNTIME_SYSTEM_PYTHON,
            "transport": "stdio",
        },
        "operational_adoption": "qualification_pending",
        "operational_profile": _compact_operational_profile(entries),
        "operational_source_authority": OPERATIONAL_SOURCE_AUTHORITY,
        "operational_source_binding": {
            "binding_kind": "immutable_SOURCE_MANIFEST",
            "binding_required_at": "source_verification",
            "binding_present": True,
            "manifest_path": "SOURCE_MANIFEST.json",
            "source_root": OPERATIONAL_SOURCE_ROOT,
            "self_content_binding": {
                "algorithm": "sha256",
                "scope": "canonical_SOURCE_MANIFEST_without_self_digest",
                "digest": "",
            },
        },
        "operational_source_root": OPERATIONAL_SOURCE_ROOT,
        "predecessor_journal": {
            "status": "source_only",
            "versions": list(PREDECESSOR_SOURCE_ONLY_VERSIONS),
        },
        "rollback": {
            "identity": ROLLBACK_IDENTITY,
            "installed_version": ROLLBACK_VERSION,
        },
        "operational_profile_index": {
            "digest": hashlib.sha256(_json_bytes(profile_index)).hexdigest(),
            "identity": profile_index["identity"],
            "path": OPERATIONAL_PROFILE_INDEX_PATH,
            "schema": profile_index["schema"],
        },
        "legacy_maestro_compatibility": dict(LEGACY_MAESTRO_COMPATIBILITY),
        "maestro_prompt_context": prompt_context_binding,
        "maestro_skill_source": _maestro_skill_source_binding(),
        "quarantined_exclusions": sorted(QUARANTINED_SKILLS),
        "required_admitted_skills": sorted(REQUIRED_ADMITTED_SKILLS),
        "sdo_producer": runtime_binding["sdo_producer"],
        "schema": SOURCE_SCHEMA,
        "skills": {
            "files": list(entries),
            "recursive_file_count": len(entries),
            "skill_count": EXPECTED_SKILL_COUNT,
            "sorted_recursive_file_sha256_stream_digest": _skill_closure_digest(
                entries
            ),
            "source_root": OPERATIONAL_SOURCE_ROOT,
            "stream_format": "lowercase_sha256_two_spaces_repo_relative_path_lf",
        },
        "release_note": PLUGIN_RELEASE_NOTE,
        "version": PLUGIN_VERSION,
    }
    manifest["operational_source_binding"]["self_content_binding"]["digest"] = (
        _source_manifest_self_digest(manifest)
    )
    return manifest


def _source_manifest_self_digest(manifest: dict) -> str:
    """Hash the canonical manifest with only its self digest blanked."""

    candidate = json.loads(json.dumps(manifest))
    try:
        candidate["operational_source_binding"]["self_content_binding"]["digest"] = ""
    except (KeyError, TypeError):
        raise DistributionError("operational source self binding is malformed")
    return hashlib.sha256(_json_bytes(candidate)).hexdigest()


def _verify_source_manifest_self_binding(manifest: dict) -> None:
    binding = manifest.get("operational_source_binding")
    self_binding = binding.get("self_content_binding") if isinstance(binding, dict) else None
    if (
        not isinstance(self_binding, dict)
        or self_binding.get("algorithm") != "sha256"
        or self_binding.get("scope") != "canonical_SOURCE_MANIFEST_without_self_digest"
        or not isinstance(self_binding.get("digest"), str)
        or self_binding["digest"] != _source_manifest_self_digest(manifest)
    ):
        raise DistributionError("source manifest drift: operational source self binding drift")


def _read_json(path: Path, *, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DistributionError(f"{label} missing: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DistributionError(f"{label} is not valid JSON: {path}: {exc}") from exc


def _assert_expected_json(path: Path, expected: object, *, label: str) -> None:
    observed = _read_json(path, label=label)
    if observed != expected:
        raise DistributionError(f"{label} drift: {path}")


def _verify_declared_paths(source_manifest: dict) -> None:
    for channel, channel_record in source_manifest.get("channels", {}).items():
        if not isinstance(channel_record, dict):
            raise DistributionError(f"channel record must be an object: {channel}")
        for field in ("manifest", "mcp", "skills"):
            value = channel_record.get(field)
            if not isinstance(value, str):
                raise DistributionError(f"channel path missing: {channel}.{field}")
            _validate_relative_path(value, label=f"{channel}.{field}")
    skills = source_manifest.get("skills")
    if not isinstance(skills, dict):
        raise DistributionError("source manifest skills record missing")
    for entry in skills.get("files", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise DistributionError(
                "source manifest contains an invalid skill file row"
            )
        _validate_relative_path(entry["path"], label="source manifest skill file")
    profile_index = source_manifest.get("operational_profile_index")
    if not isinstance(profile_index, dict) or not isinstance(
        profile_index.get("path"), str
    ):
        raise DistributionError("source manifest operational profile index missing")
    _validate_relative_path(
        profile_index["path"], label="source manifest operational profile index"
    )


def verify_bundle(
    bundle_root: Path,
    source_skills: Path | None = None,
    *,
    allowed_runtime_markers: frozenset[str] = frozenset(),
    runtime_root: Path | None = None,
) -> dict:
    bundle_root = bundle_root.absolute()
    source_skills = (source_skills or default_source_skills()).absolute()
    if not allowed_runtime_markers <= ALLOWED_INSTALLED_RUNTIME_MARKERS:
        unexpected = sorted(allowed_runtime_markers - ALLOWED_INSTALLED_RUNTIME_MARKERS)
        raise DistributionError(
            f"unadmitted installed runtime marker request: {unexpected}"
        )
    source_entries = verify_source_skills(source_skills)
    verify_runtime_baseline()
    _reject_symlinks(bundle_root, label="bundle")

    top_level = frozenset(path.name for path in bundle_root.iterdir())
    expected_top_level = ALLOWED_BUNDLE_TOP_LEVEL | allowed_runtime_markers
    if top_level != expected_top_level:
        extra = sorted(top_level - expected_top_level)
        missing = sorted(expected_top_level - top_level)
        raise DistributionError(
            f"bundle top-level mismatch: extra={extra}, missing={missing}"
        )

    source_manifest_path = bundle_root / "SOURCE_MANIFEST.json"
    observed_source = _read_json(source_manifest_path, label="source manifest")
    if not isinstance(observed_source, dict):
        raise DistributionError("source manifest must be a JSON object")
    _verify_declared_paths(observed_source)
    _verify_source_manifest_self_binding(observed_source)
    expected_binding = _runtime_binding(runtime_root)
    expected_profile_index = _operational_profile_index(
        source_skills, source_entries
    )
    expected_source = _source_manifest(
        source_entries,
        expected_binding,
        expected_profile_index,
        _maestro_prompt_context_binding(
            bundle_root / MAESTRO_PROMPT_CONTEXT_ROOT
        ),
    )
    if observed_source != expected_source:
        raise DistributionError("source manifest drift")

    _assert_expected_json(
        bundle_root / ".codex-plugin" / "plugin.json",
        _codex_plugin_manifest(),
        label="Codex plugin manifest",
    )
    _assert_expected_json(
        bundle_root / ".claude-plugin" / "plugin.json",
        _claude_plugin_manifest(),
        label="Claude plugin manifest",
    )
    _assert_expected_json(
        bundle_root / CODEX_HOOKS_PATH,
        _codex_hooks_manifest(),
        label="Codex hook manifest",
    )
    _assert_expected_json(
        bundle_root / ".mcp.json", _mcp_manifest(), label="MCP manifest"
    )
    _assert_expected_json(
        bundle_root / OPERATIONAL_PROFILE_INDEX_PATH,
        expected_profile_index,
        label="operational profile index",
    )
    _assert_expected_json(
        bundle_root / RUNTIME_BINDING_PATH,
        expected_binding,
        label="runtime locator binding",
    )
    wrapper = bundle_root / RUNTIME_WRAPPER_PATH
    if wrapper.is_symlink() or not wrapper.is_file():
        raise DistributionError("portable Hermes MCP wrapper is unavailable")
    if stat.S_IMODE(wrapper.stat().st_mode) != 0o755:
        raise DistributionError("portable Hermes MCP wrapper mode drift")
    if not wrapper.read_bytes().startswith(b"#!/usr/bin/python3\n"):
        raise DistributionError("portable Hermes MCP wrapper shebang drift")
    if _sha256_file(wrapper) != _sha256_file(runtime_launcher()):
        raise DistributionError("portable Hermes MCP wrapper content drift")
    probe_python_candidates = [Path(RUNTIME_SYSTEM_PYTHON)]
    admitted_python = runtime_python()
    if admitted_python.is_file() and admitted_python not in probe_python_candidates:
        probe_python_candidates.append(admitted_python)
    probe = None
    probe_code = (
        "import os,runpy,sys; script=sys.argv[1]; args=sys.argv[2:]; "
        "sys.argv=[script,*args]; "
        "sys.path[:0]=[p for p in os.environ.get('PYTHONPATH','').split(os.pathsep) if p]; "
        "runpy.run_path(script, run_name='__main__')"
    )
    for probe_python in probe_python_candidates:
        probe = subprocess.run(
            [
                str(probe_python),
                "-I",
                "-c",
                probe_code,
                str(wrapper),
                "--orch-runtime-origin-probe",
            ],
            cwd=Path("/private/tmp"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        if probe.returncode == 0:
            break
    if probe is None or probe.returncode != 0:
        raise DistributionError(
            "portable Hermes runtime locator cannot resolve the operational MCP module"
        )

    bundle_skills = bundle_root / "skills"
    bundle_entries = _skill_entries(bundle_skills)
    bundle_names = _skill_names(bundle_skills)
    quarantined = sorted(bundle_names & QUARANTINED_SKILLS)
    if quarantined:
        raise DistributionError(
            "quarantined skill included in bundle: " + ", ".join(quarantined)
        )
    if bundle_entries != source_entries:
        source_map = {entry["path"]: entry["sha256"] for entry in source_entries}
        bundle_map = {entry["path"]: entry["sha256"] for entry in bundle_entries}
        extra = sorted(bundle_map.keys() - source_map.keys())
        missing = sorted(source_map.keys() - bundle_map.keys())
        drift = sorted(
            path
            for path in source_map.keys() & bundle_map.keys()
            if source_map[path] != bundle_map[path]
        )
        raise DistributionError(
            f"skill mirror mismatch: extra={extra}, missing={missing}, drift={drift}"
        )

    return {
        "bundle": str(bundle_root),
        "identity": PLUGIN_ID,
        "mcp_module": MCP_MODULE,
        "recursive_file_count": len(bundle_entries),
        "skill_count": len(bundle_names),
        "skill_closure_digest": _skill_closure_digest(bundle_entries),
        "status": "verified",
    }


def verify_installed_bundle(
    bundle_root: Path, source_skills: Path | None = None
) -> dict:
    """Verify one active installed cache against the admitted source closure.

    Host-owned cache markers are not distributable content.  Only the current
    active marker is admitted here; an orphaned cache or any same-version
    content difference remains a typed verification failure.
    """

    bundle_root = bundle_root.absolute()
    if (bundle_root / ORPHANED_INSTALLED_MARKER).exists():
        raise DistributionError(f"installed bundle is orphaned: {bundle_root}")
    if bundle_root.name != PLUGIN_VERSION:
        raise DistributionError(
            "installed bundle version path mismatch: expected "
            f"{PLUGIN_VERSION}, observed {bundle_root.name}"
        )
    if bundle_root.parent.name != PLUGIN_ID:
        raise DistributionError(
            "installed bundle plugin path mismatch: expected "
            f"{PLUGIN_ID}, observed {bundle_root.parent.name}"
        )
    if bundle_root.parent.parent.name != MARKETPLACE_ID:
        raise DistributionError(
            "installed bundle marketplace path mismatch: expected "
            f"{MARKETPLACE_ID}, observed {bundle_root.parent.parent.name}"
        )

    present_markers = frozenset(
        marker
        for marker in ALLOWED_INSTALLED_RUNTIME_MARKERS
        if (bundle_root / marker).exists()
    )
    result = verify_bundle(
        bundle_root,
        source_skills,
        allowed_runtime_markers=present_markers,
        runtime_root=_repo_root(),
    )
    result.update({
        "installed_cache": True,
        "runtime_markers": sorted(present_markers),
        "version": PLUGIN_VERSION,
    })
    return result


def verify_marketplace(marketplace_root: Path) -> dict:
    marketplace_root = marketplace_root.absolute()
    _assert_expected_json(
        marketplace_root / ".agents" / "plugins" / "marketplace.json",
        _codex_marketplace_manifest(),
        label="Codex marketplace manifest",
    )
    _assert_expected_json(
        marketplace_root / ".claude-plugin" / "marketplace.json",
        _claude_marketplace_manifest(),
        label="Claude marketplace manifest",
    )
    bundle = marketplace_root / "distribution" / PLUGIN_ID
    if not bundle.is_dir() or bundle.is_symlink():
        raise DistributionError("marketplace plugin source must be a real directory")
    return {
        "identity": MARKETPLACE_ID,
        "plugin": PLUGIN_ID,
        "status": "verified",
    }


def _write_bytes(path: Path, content: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)


def _populate_stage(
    stage: Path,
    source_skills: Path,
    *,
    runtime_root: Path | None = None,
    maestro_source_repo: Path | None = None,
) -> None:
    entries = verify_source_skills(source_skills)
    shutil.copytree(
        source_skills,
        stage / "skills",
        copy_function=shutil.copy2,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    prompt_context_root = stage / MAESTRO_PROMPT_CONTEXT_ROOT
    _materialize_maestro_prompt_context(prompt_context_root, maestro_source_repo)
    binding = _runtime_binding(runtime_root)
    _write_bytes(
        stage / ".codex-plugin" / "plugin.json", _json_bytes(_codex_plugin_manifest())
    )
    _write_bytes(
        stage / ".claude-plugin" / "plugin.json", _json_bytes(_claude_plugin_manifest())
    )
    _write_bytes(stage / CODEX_HOOKS_PATH, _json_bytes(_codex_hooks_manifest()))
    _write_bytes(stage / ".mcp.json", _json_bytes(_mcp_manifest()))
    profile_index = _operational_profile_index(stage / "skills", entries)
    _write_bytes(
        stage / OPERATIONAL_PROFILE_INDEX_PATH,
        _json_bytes(profile_index),
    )
    _write_bytes(
        stage / RUNTIME_WRAPPER_PATH,
        runtime_launcher().read_bytes(),
        mode=0o755,
    )
    for relative in SDO_PRODUCER_MIRROR_FILES:
        source = _repo_root() / SDO_PRODUCER_MIRROR_ROOT / relative
        _write_bytes(
            stage / SDO_PRODUCER_MIRROR_ROOT / relative,
            source.read_bytes(),
        )
    _write_bytes(
        stage / RUNTIME_BINDING_PATH,
        _json_bytes(binding),
    )
    _write_bytes(
        stage / "SOURCE_MANIFEST.json",
        _json_bytes(
            _source_manifest(
                entries,
                binding,
                profile_index,
                _maestro_prompt_context_binding(prompt_context_root),
            )
        ),
    )


@contextmanager
def _target_lock(target: Path) -> Iterator[None]:
    lock_path = target.parent / f".{target.name}.distribution.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise DistributionError(
            f"another distribution writer holds {lock_path}"
        ) from exc
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _atomic_replace(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _remove_tree(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def transactional_sync(
    source_skills: Path,
    target: Path,
    *,
    runtime_root: Path | None = None,
    maestro_source_repo: Path | None = None,
) -> dict:
    source_skills = source_skills.absolute()
    target = target.absolute()
    if source_skills.is_relative_to(target) or target.is_relative_to(source_skills):
        raise DistributionError("source and target must be disjoint directories")
    if target.is_symlink():
        raise DistributionError(f"target must not be a symlink: {target}")
    if target.exists() and not target.is_dir():
        raise DistributionError(f"existing target must be a directory: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise DistributionError(f"target parent must not be a symlink: {target.parent}")

    with _target_lock(target):
        stage = Path(
            tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=target.parent)
        )
        rollback = Path(
            tempfile.mkdtemp(prefix=f".{target.name}.rollback-", dir=target.parent)
        )
        rollback.rmdir()
        prior_present = target.exists()
        moved_prior = False
        published = False
        try:
            _populate_stage(
                stage,
                source_skills,
                runtime_root=runtime_root,
                maestro_source_repo=maestro_source_repo,
            )
            verify_bundle(stage, source_skills, runtime_root=runtime_root)
            if prior_present:
                _atomic_replace(target, rollback)
                moved_prior = True
            try:
                _atomic_replace(stage, target)
                published = True
                result = verify_bundle(target, source_skills, runtime_root=runtime_root)
            except BaseException:
                if published and target.exists():
                    failed = Path(
                        tempfile.mkdtemp(
                            prefix=f".{target.name}.failed-", dir=target.parent
                        )
                    )
                    failed.rmdir()
                    _atomic_replace(target, failed)
                    _remove_tree(failed)
                if moved_prior and rollback.exists():
                    _atomic_replace(rollback, target)
                raise
            if rollback.exists():
                _remove_tree(rollback)
            return result
        finally:
            if stage.exists():
                _remove_tree(stage)
            if rollback.exists():
                if moved_prior and not target.exists():
                    _atomic_replace(rollback, target)
                elif rollback.exists():
                    _remove_tree(rollback)


def transactional_install(source_skills: Path, target: Path) -> dict:
    """Materialize one reversible installed bundle from the admitted source.

    The caller selects only the installation target.  Runtime identity is
    derived internally from this verified checkout and cannot be replaced by
    a caller-provided launcher, interpreter, or source root.
    """

    return transactional_sync(
        source_skills,
        target,
        runtime_root=_repo_root(),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("sync", "install", "verify", "verify-installed"):
        child = subparsers.add_parser(command)
        child.add_argument(
            "--source-skills", type=Path, default=default_source_skills()
        )
        child.add_argument("--target", type=Path, default=default_bundle_target())
        if command == "sync":
            child.add_argument("--maestro-source-repo", type=Path)
    subparsers.add_parser("preflight-latest-stable")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "preflight-latest-stable":
            result = verify_latest_stable_release()
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "sync":
            result = transactional_sync(
                args.source_skills,
                args.target,
                maestro_source_repo=args.maestro_source_repo,
            )
            if args.target.absolute() == default_bundle_target().absolute():
                _write_bytes(
                    _repo_root() / ".agents" / "plugins" / "marketplace.json",
                    _json_bytes(_codex_marketplace_manifest()),
                )
                _write_bytes(
                    _repo_root() / ".claude-plugin" / "marketplace.json",
                    _json_bytes(_claude_marketplace_manifest()),
                )
        elif args.command == "install":
            result = transactional_install(args.source_skills, args.target)
        elif args.command == "verify-installed":
            verify_latest_stable_release()
            result = verify_installed_bundle(args.target, args.source_skills)
        else:
            result = verify_bundle(args.target, args.source_skills)
        marketplace = verify_marketplace(_repo_root())
        result["marketplace"] = marketplace
    except DistributionError as exc:
        print(json.dumps({"error": str(exc), "status": "failed"}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
