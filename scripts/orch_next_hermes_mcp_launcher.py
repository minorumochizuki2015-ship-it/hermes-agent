#!/usr/bin/python3
"""Fail-closed launcher for the admitted Hermes operational MCP server."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
from copy import deepcopy
from pathlib import Path, PurePosixPath
import runpy
import stat
import subprocess
import sys
import time
import types


MCP_MODULE = "agent.transports.hermes_tools_mcp_server"
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
EXPECTED_ORIGIN = REPO_ROOT / "agent" / "transports" / "hermes_tools_mcp_server.py"
RUNTIME_LOCATOR_SCHEMA = "orch-next-hermes-runtime-locator.v1"
RUNTIME_LOCATOR_MODE_PORTABLE = "manifest_relative"
RUNTIME_LOCATOR_MODE_INSTALLED = "installer_materialized"
RUNTIME_PORTABLE_SOURCE_ROOT = "../../.."
RUNTIME_BINDING_NAME = "RUNTIME_BINDING.json"
SOURCE_MANIFEST_NAME = "SOURCE_MANIFEST.json"
GIT = "/usr/bin/git"
PLUGIN_ID = "orch-next-hermes-harness"
PLUGIN_VERSION = "0.1.47"
SYSTEM_PYTHON = "/usr/bin/python3"
AUTHORITY_BUNDLE_DIGEST = (
    "7d6bc36e50938f74ad2728ed3d87f272620086de7bfd928616c84bbdfd09412e"
)
AUTHORITY_SOURCE = "scripts/ops/mk_whole_goal_control.py"
AUTHORITY_SOURCE_REVISION = "c25555b54315b8dc868d12b8699b500b9aab8094"
AUTHORITY_SOURCE_SHA256 = (
    "1183c28805e3a35033172505d9616ef247222d4e6cb5dd3425363c51b3d9615b"
)
AUTHORITY_CONSUMER_PATH = "tui_gateway/maestro_authority.py"
AUTHORITY_CONSUMER_SHA256 = (
    "182f1895a61a4cde2b2002b0f31f0087ac2a2de2f33698afc7c9f062c48532ab"
)
MINIMUM_SOURCE_REVISION = "8585d5d9de143750e85629000e62576a1e082169"
SKILL_CLOSURE_DIGEST = (
    "c869e171d1cb15c6e5004642b9db3a51fa19033471cc824a65566269ab073329"
)
UPSTREAM_COMMIT = "f80f453ae0679347e38abc917c7f94f717bf96c5"
UPSTREAM_TAG = "v2026.8.13"
UPSTREAM_VERSION = "0.20.1"
RUNTIME_LAUNCHER_PATH = "scripts/orch_next_hermes_mcp_launcher.py"
RUNTIME_PYTHON_PATH = ".venv/bin/python"
SDO_PRODUCER_MIRROR_ROOT = "runtime/sdo_producer"
SDO_PRODUCER_SOURCE_REVISION = "c25555b54315b8dc868d12b8699b500b9aab8094"
SDO_PRODUCER_SOURCE_TREE = "ba7e28fef29e9a28c93ff9226f260e74bc061e3c"
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
RUNTIME_PROVENANCE_MANIFEST_FLAG = "--orch-runtime-provenance-manifest"
RUNTIME_ORIGIN_PROBE_FLAG = "--orch-runtime-origin-probe"
LIFECYCLE_SERVICE_FLAG = "--orch-lifecycle-service"
LIFECYCLE_SERVICE_PATH = "scripts/orch_next_hermes_serve_service.py"
LIFECYCLE_SOURCE_LOCK_ENV = "ORCH_LIFECYCLE_SOURCE_LOCK_FD"
ADMITTED_RUNTIME_FILES = (
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
EXPECTED_BINDING_KEYS = frozenset({
    "authority_bundle_digest",
    "authority_source",
    "authority_source_revision",
    "authority_source_sha256",
    "minimum_source_revision",
    "mode",
    "plugin_id",
    "plugin_version",
    "rollback_identity",
    "runtime_files",
    "runtime_files_digest",
    "runtime_launcher",
    "runtime_python",
    "runtime_python_sha256",
    "runtime_python_size",
    "schema",
    "skill_closure_digest",
    "source_root",
    "upstream_commit",
    "upstream_tag",
    "upstream_version",
    "sdo_producer",
})


def _fail(message: str) -> None:
    raise SystemExit(message)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_object(path: Path, *, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(f"{label} is unavailable or invalid")
    if type(value) is not dict:
        _fail(f"{label} must be an object")
    return value


def _relative_path(value: object, *, label: str) -> PurePosixPath:
    if type(value) is not str or not value or "\\" in value:
        _fail(f"{label} is not an admitted relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or "." in path.parts or ".." in path.parts:
        _fail(f"{label} is not an admitted relative path")
    return path


def _has_symlink_component(root: Path, relative: PurePosixPath) -> bool:
    current = root
    for part in relative.parts:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except OSError:
            return False
    return False


def _source_file(
    source_root: Path,
    value: object,
    *,
    label: str,
    executable: bool = False,
    allow_final_symlink: bool = False,
) -> Path:
    relative = _relative_path(value, label=label)
    candidate = source_root.joinpath(*relative.parts)
    if not candidate.exists() or not candidate.is_file():
        _fail(f"{label} is unavailable")
    if _has_symlink_component(source_root, relative):
        if not allow_final_symlink or not candidate.is_symlink():
            _fail(f"{label} contains a symlink or alias escape")
        parent_relative = PurePosixPath(*relative.parts[:-1])
        if parent_relative.parts and _has_symlink_component(
            source_root, parent_relative
        ):
            _fail(f"{label} contains a symlink or alias escape")
    lexical = candidate.absolute()
    if not lexical.is_relative_to(source_root):
        _fail(f"{label} escapes the admitted source root")
    if executable and not os.access(candidate, os.X_OK):
        _fail(f"{label} is not executable")
    return candidate


def _runtime_files_digest(entries: list[dict]) -> str:
    stream = "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in entries)
    return hashlib.sha256(stream.encode("utf-8")).hexdigest()


def _verified_runtime_interpreter(
    source_root: Path, binding: dict
) -> tuple[Path, Path]:
    """Resolve and byte-bind the interpreter selected by the admitted venv link."""

    lexical = _source_file(
        source_root,
        binding.get("runtime_python"),
        label="runtime interpreter",
        executable=True,
        allow_final_symlink=True,
    )
    try:
        resolved = lexical.resolve(strict=True)
        descriptor = os.open(
            resolved,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError:
        _fail("runtime interpreter identity is unavailable")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
            _fail("runtime interpreter identity is unavailable")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_mode != after.st_mode
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
        or binding.get("runtime_python_size") != after.st_size
        or binding.get("runtime_python_sha256") != digest.hexdigest()
        or not os.access(resolved, os.X_OK)
    ):
        _fail("runtime interpreter identity drift")
    # Keep the lexical path only as argv[0] so CPython preserves its virtual-
    # environment prefix. The kernel executes the already-resolved target;
    # retargeting the venv symlink after this check therefore cannot select a
    # different executable.
    return lexical, resolved


def _source_root(bundle_root: Path, binding: dict) -> Path:
    mode = binding.get("mode")
    value = binding.get("source_root")
    if mode == RUNTIME_LOCATOR_MODE_PORTABLE:
        if value != RUNTIME_PORTABLE_SOURCE_ROOT:
            _fail("portable runtime locator root drift")
        candidate = bundle_root / "runtime" / value
    elif mode == RUNTIME_LOCATOR_MODE_INSTALLED:
        if type(value) is not str or not Path(value).is_absolute():
            _fail("installed runtime locator root is not canonical")
        candidate = Path(value)
        try:
            if candidate.absolute() != candidate.resolve(strict=True):
                _fail("installed runtime locator root contains a symlink or alias")
        except OSError:
            _fail("installed runtime locator root is unavailable")
    else:
        _fail("runtime locator mode is not admitted")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        _fail("runtime locator source root is unavailable")
    if not resolved.is_dir():
        _fail("runtime locator source root is unavailable")
    return resolved


def _verify_git_lineage(source_root: Path, binding: dict) -> None:
    if not Path(GIT).is_file() or not os.access(GIT, os.X_OK):
        _fail("fixed Git verifier is unavailable")
    for revision, label in (
        (binding.get("upstream_commit"), "upstream revision"),
        (binding.get("minimum_source_revision"), "source revision"),
    ):
        if type(revision) is not str or len(revision) != 40:
            _fail(f"{label} is invalid")
        command = (
            [GIT, "merge-base", "--is-ancestor", revision, "HEAD"]
            if label == "upstream revision"
            else [GIT, "cat-file", "-e", f"{revision}^{{commit}}"]
        )
        completed = subprocess.run(
            command,
            cwd=source_root,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        if completed.returncode != 0:
            _fail(f"{label} is not admitted by the current source")


def _expected_sdo_producer_binding() -> dict:
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
        "files": [
            {"path": path, "sha256": SDO_PRODUCER_MIRROR_DIGESTS[path]}
            for path in SDO_PRODUCER_MIRROR_FILES
        ],
    }


def _verify_sdo_producer_binding(source_root: Path, binding: dict, manifest: dict) -> None:
    expected = _expected_sdo_producer_binding()
    if binding.get("sdo_producer") != expected:
        _fail("SDO producer binding drift")
    if manifest.get("sdo_producer") != expected:
        _fail("source manifest SDO producer binding drift")
    root_relative = _relative_path(
        expected["root"], label="SDO producer mirror root"
    )
    mirror_root = source_root.joinpath(*root_relative.parts)
    if not mirror_root.is_dir() or _has_symlink_component(source_root, root_relative):
        _fail("SDO producer mirror root is unavailable")
    for entry in expected["files"]:
        path = _source_file(
            mirror_root,
            entry["path"],
            label="SDO producer mirror file",
        )
        if _sha256(path) != entry["sha256"]:
            _fail("SDO producer mirror content drift")


def _verify_binding(
    bundle_root: Path,
    *,
    runtime_dir: Path | None = None,
    expected_source_root: Path | None = None,
) -> tuple[Path, Path, Path]:
    runtime_dir = (
        Path(__file__).resolve().parent if runtime_dir is None else runtime_dir
    )
    binding_path = runtime_dir / RUNTIME_BINDING_NAME
    manifest_path = bundle_root / SOURCE_MANIFEST_NAME
    binding = _json_object(binding_path, label="runtime locator binding")
    manifest = _json_object(manifest_path, label="source manifest")
    if frozenset(binding) != EXPECTED_BINDING_KEYS:
        _fail("runtime locator binding fields drift")
    if binding.get("schema") != RUNTIME_LOCATOR_SCHEMA:
        _fail("runtime locator schema drift")
    expected_scalars = {
        "authority_bundle_digest": AUTHORITY_BUNDLE_DIGEST,
        "authority_source": AUTHORITY_SOURCE,
        "authority_source_revision": AUTHORITY_SOURCE_REVISION,
        "authority_source_sha256": AUTHORITY_SOURCE_SHA256,
        "minimum_source_revision": MINIMUM_SOURCE_REVISION,
        "plugin_id": PLUGIN_ID,
        "plugin_version": PLUGIN_VERSION,
        "runtime_launcher": RUNTIME_LAUNCHER_PATH,
        "runtime_python": RUNTIME_PYTHON_PATH,
        "skill_closure_digest": SKILL_CLOSURE_DIGEST,
        "upstream_commit": UPSTREAM_COMMIT,
        "upstream_tag": UPSTREAM_TAG,
        "upstream_version": UPSTREAM_VERSION,
    }
    if any(binding.get(field) != value for field, value in expected_scalars.items()):
        _fail("runtime locator scalar binding drift")
    mcp = manifest.get("mcp")
    locator = mcp.get("locator") if type(mcp) is dict else None
    if type(locator) is not dict:
        _fail("source manifest runtime locator is unavailable")
    if locator.get("binding") != f"runtime/{RUNTIME_BINDING_NAME}":
        _fail("source manifest runtime binding path drift")
    if locator.get("binding_sha256") != _sha256(binding_path):
        _fail("source manifest runtime binding digest drift")
    if locator.get("mode") != binding.get("mode"):
        _fail("source manifest runtime locator mode drift")
    if locator.get("rollback_identity") != binding.get("rollback_identity"):
        _fail("source manifest rollback identity drift")
    runtime = manifest.get("hermes_agent_runtime")
    if type(runtime) is not dict or (
        runtime.get("version") != binding.get("upstream_version")
        or runtime.get("upstream_tag") != binding.get("upstream_tag")
        or runtime.get("upstream_commit") != binding.get("upstream_commit")
    ):
        _fail("source manifest upstream binding drift")
    skills = manifest.get("skills")
    if type(skills) is not dict or skills.get(
        "sorted_recursive_file_sha256_stream_digest"
    ) != binding.get("skill_closure_digest"):
        _fail("source manifest content digest drift")
    source_root = _source_root(bundle_root, binding)
    _verify_sdo_producer_binding(source_root, binding, manifest)
    if expected_source_root is not None:
        try:
            expected_root = expected_source_root.resolve(strict=True)
        except OSError:
            _fail("runtime locator expected source root is unavailable")
        if source_root != expected_root:
            _fail("runtime locator source root drift")
    entries = binding.get("runtime_files")
    if type(entries) is not list or not entries:
        _fail("runtime locator content binding is unavailable")
    if (
        tuple(entry.get("path") if type(entry) is dict else None for entry in entries)
        != ADMITTED_RUNTIME_FILES
    ):
        _fail("runtime locator admitted file set drift")
    observed: list[dict] = []
    for entry in entries:
        if type(entry) is not dict or frozenset(entry) != {"path", "sha256"}:
            _fail("runtime locator content row is invalid")
        path = _source_file(source_root, entry.get("path"), label="runtime file")
        digest = entry.get("sha256")
        if type(digest) is not str or len(digest) != 64 or _sha256(path) != digest:
            _fail("runtime locator content digest drift")
        observed.append({"path": entry["path"], "sha256": digest})
    runtime_files_digest = _runtime_files_digest(observed)
    if runtime_files_digest != binding.get("runtime_files_digest"):
        _fail("runtime locator aggregate content digest drift")
    if binding.get("rollback_identity") != f"installed:{PLUGIN_ID}@0.1.42":
        _fail("runtime locator rollback identity drift")
    if _sha256(Path(__file__)) != next(
        (
            entry["sha256"]
            for entry in observed
            if entry["path"] == binding.get("runtime_launcher")
        ),
        None,
    ):
        _fail("portable runtime wrapper content drift")
    _verify_git_lineage(source_root, binding)
    runtime_launcher = _source_file(
        source_root,
        binding.get("runtime_launcher"),
        label="runtime launcher",
    )
    if (
        type(binding.get("runtime_python_sha256")) is not str
        or len(binding["runtime_python_sha256"]) != 64
        or type(binding.get("runtime_python_size")) is not int
        or binding["runtime_python_size"] <= 0
    ):
        _fail("runtime interpreter identity binding is invalid")
    runtime_python, runtime_python_target = _verified_runtime_interpreter(
        source_root, binding
    )
    return runtime_python, runtime_python_target, runtime_launcher


def verified_lifecycle_runtime_provenance(
    bundle_root: Path,
    *,
    expected_source_root: Path,
) -> tuple[dict, str]:
    """Return provenance only after the admitted portable locator verifies.

    Lifecycle callers import this source module before their final mutation
    boundary, then bind the checked-in portable bundle to the exact source
    root.  The existing binding verifier remains the single authority for the
    runtime-file set, per-file digests, aggregate digest, lineage, interpreter,
    rollback identity, and source-manifest locator.
    """

    try:
        lexical_bundle = bundle_root.absolute()
        resolved_bundle = lexical_bundle.resolve(strict=True)
        runtime_dir = (lexical_bundle / "runtime").resolve(strict=True)
    except OSError:
        _fail("portable lifecycle runtime locator is unavailable")
    if lexical_bundle != resolved_bundle or runtime_dir.parent != resolved_bundle:
        _fail("portable lifecycle runtime locator is unavailable")
    try:
        module_root = REPO_ROOT.resolve(strict=True)
        expected_root = expected_source_root.resolve(strict=True)
    except OSError:
        _fail("portable lifecycle runtime locator is unavailable")
    if module_root != expected_root:
        _fail("portable lifecycle runtime source root drift")
    _verify_binding(
        resolved_bundle,
        runtime_dir=runtime_dir,
        expected_source_root=expected_root,
    )
    return _candidate_runtime_provenance(str(resolved_bundle / SOURCE_MANIFEST_NAME))


def _bundle_root() -> Path | None:
    runtime_dir = Path(__file__).resolve().parent
    candidate = runtime_dir.parent
    if runtime_dir.name != "runtime":
        return None
    if not (candidate / SOURCE_MANIFEST_NAME).is_file():
        return None
    return candidate


def verified_origin() -> Path:
    """Return the exact admitted module origin or fail without fallback."""

    source_root = REPO_ROOT
    bundle_root = _bundle_root()
    if bundle_root is not None:
        binding = _json_object(
            bundle_root / "runtime" / RUNTIME_BINDING_NAME,
            label="runtime locator binding",
        )
        source_root = _source_root(bundle_root, binding)
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    expected_origin = source_root / "agent" / "transports" / "hermes_tools_mcp_server.py"
    if not expected_origin.is_file():
        _fail("Hermes MCP module origin is outside the admitted checkout")
    spec = importlib.util.find_spec(MCP_MODULE)
    if spec is None or spec.origin is None:
        _fail("Hermes MCP module is unavailable from the admitted runtime")
    origin = Path(spec.origin).resolve()
    if origin != expected_origin.resolve():
        _fail("Hermes MCP module origin is outside the admitted checkout")
    return origin


def verified_startup() -> None:
    """Initialize the real MCP surface without network, credentials, or serving."""

    verified_origin()
    exact_modules = {
        MCP_MODULE: EXPECTED_ORIGIN,
        "hermes_cli.env_loader": REPO_ROOT / "hermes_cli" / "env_loader.py",
        "hermes_constants": REPO_ROOT / "hermes_constants.py",
    }
    loaded: dict[str, object] = {}
    for name, expected in exact_modules.items():
        module = importlib.import_module(name)
        origin = Path(str(getattr(module, "__file__", ""))).resolve()
        if origin != expected.resolve():
            _fail(f"{name} origin is outside the admitted checkout")
        loaded[name] = module
    build_server = getattr(loaded[MCP_MODULE], "_build_server", None)
    if not callable(build_server):
        _fail("Hermes MCP startup surface is unavailable")
    # Tool availability discovery may probe optional paid/network providers.
    # The current upstream builder accepts an explicit catalog, so the origin
    # check can construct the real FastMCP surface without importing
    # model_tools or discovering any optional provider tools.
    try:
        server = build_server(tool_definitions=[])
    except TypeError as exc:
        _fail(f"Hermes MCP startup surface has no verification catalog: {exc}")
    manager = getattr(server, "_tool_manager", None)
    observed_tools = getattr(manager, "_tools", None)
    if (
        type(observed_tools) is not dict
        or observed_tools
    ):
        _fail("Hermes MCP dry-run optional tool surface drift")


def _runtime_head() -> str:
    """Return this exact clean checkout revision or fail closed."""

    if not Path(GIT).is_file() or not os.access(GIT, os.X_OK):
        _fail("Hermes runtime provenance authority unavailable")
    commands = (
        ([GIT, "rev-parse", "--show-toplevel"], "root"),
        (
            [
                GIT,
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
                "--",
                ".",
                ":(exclude)distribution/.orch-next-hermes-harness.distribution.lock",
            ],
            "status",
        ),
        ([GIT, "rev-parse", "--verify", "HEAD^{commit}"], "head"),
    )
    observed: dict[str, str] = {}
    for command, label in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
        except Exception:
            _fail("Hermes runtime provenance authority unavailable")
        if completed.returncode != 0:
            _fail("Hermes runtime provenance authority unavailable")
        observed[label] = completed.stdout.strip()
    try:
        root_matches = Path(observed["root"]).resolve(strict=True) == REPO_ROOT.resolve(
            strict=True
        )
    except OSError:
        root_matches = False
    if (
        not root_matches
        or observed["status"]
        or len(observed["head"]) != 40
        or any(character not in "0123456789abcdef" for character in observed["head"])
    ):
        _fail("Hermes runtime provenance authority unavailable")
    return observed["head"]


def _source_manifest_self_digest(manifest: dict) -> str:
    """Hash a manifest after blanking its exact self-content digest."""

    candidate = deepcopy(manifest)
    binding = candidate.get("operational_source_binding")
    self_binding = (
        binding.get("self_content_binding") if type(binding) is dict else None
    )
    if (
        type(self_binding) is not dict
        or set(self_binding) != {"algorithm", "digest", "scope"}
    ):
        _fail("Hermes runtime provenance authority unavailable")
    self_binding["digest"] = ""
    return hashlib.sha256(
        (json.dumps(candidate, indent=2, sort_keys=True) + "\n").encode("utf-8")
    ).hexdigest()


def _verify_source_manifest_self_binding(manifest: dict) -> dict:
    binding = manifest.get("operational_source_binding")
    self_binding = (
        binding.get("self_content_binding") if type(binding) is dict else None
    )
    if (
        type(self_binding) is not dict
        or set(self_binding) != {"algorithm", "digest", "scope"}
        or self_binding.get("algorithm") != "sha256"
        or self_binding.get("scope")
        != "canonical_SOURCE_MANIFEST_without_self_digest"
        or type(self_binding.get("digest")) is not str
        or self_binding["digest"] != _source_manifest_self_digest(manifest)
    ):
        _fail("Hermes runtime provenance authority unavailable")
    return self_binding


def _canonical_runtime_manifest_path(manifest: dict) -> Path:
    """Project a source or already-verified installed manifest to source bytes."""

    canonical_path = _source_file(
        REPO_ROOT,
        f"distribution/{PLUGIN_ID}/{SOURCE_MANIFEST_NAME}",
        label="canonical source manifest",
    )
    canonical_manifest = _json_object(
        canonical_path, label="canonical source manifest"
    )
    canonical_self_binding = _verify_source_manifest_self_binding(canonical_manifest)
    if manifest == canonical_manifest:
        return canonical_path
    _verify_source_manifest_self_binding(manifest)

    canonical_mcp = canonical_manifest.get("mcp")
    mcp = manifest.get("mcp")
    canonical_locator = (
        canonical_mcp.get("locator") if type(canonical_mcp) is dict else None
    )
    locator = mcp.get("locator") if type(mcp) is dict else None
    if (
        type(canonical_locator) is not dict
        or type(locator) is not dict
        or set(locator) != set(canonical_locator)
        or locator.get("mode") != RUNTIME_LOCATOR_MODE_INSTALLED
    ):
        _fail("Hermes runtime provenance authority unavailable")

    normalized = deepcopy(manifest)
    normalized["mcp"]["locator"]["mode"] = canonical_locator["mode"]
    normalized["mcp"]["locator"]["binding_sha256"] = canonical_locator[
        "binding_sha256"
    ]
    normalized["operational_source_binding"]["self_content_binding"]["digest"] = (
        canonical_self_binding["digest"]
    )
    if normalized != canonical_manifest:
        _fail("Hermes runtime provenance authority unavailable")
    return canonical_path


def _candidate_runtime_provenance(manifest_value: object) -> tuple[dict, str]:
    if type(manifest_value) is not str or not Path(manifest_value).is_absolute():
        _fail("Hermes runtime provenance authority unavailable")
    manifest_path = Path(manifest_value)
    try:
        lexical = manifest_path.absolute()
        metadata = lexical.lstat()
        resolved = lexical.resolve(strict=True)
    except OSError:
        _fail("Hermes runtime provenance authority unavailable")
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or resolved != lexical
        or metadata.st_size <= 0
        or metadata.st_size > 1024 * 1024
    ):
        _fail("Hermes runtime provenance authority unavailable")
    source_manifest = _json_object(resolved, label="source manifest")
    runtime = source_manifest.get("hermes_agent_runtime")
    if (
        source_manifest.get("identity") != PLUGIN_ID
        or source_manifest.get("version") != PLUGIN_VERSION
        or type(runtime) is not dict
        or runtime.get("upstream_tag") != UPSTREAM_TAG
        or runtime.get("version") != UPSTREAM_VERSION
        or runtime.get("upstream_commit") != UPSTREAM_COMMIT
    ):
        _fail("Hermes runtime provenance authority unavailable")
    canonical_manifest_path = _canonical_runtime_manifest_path(source_manifest)
    candidate = {
        "upstreamReleaseTag": UPSTREAM_TAG,
        "upstreamPackageVersion": UPSTREAM_VERSION,
        "upstreamCommit": UPSTREAM_COMMIT,
        "runtimeCommit": _runtime_head(),
        "runtimeContentDigest": _sha256(canonical_manifest_path),
    }
    digest = hashlib.sha256(
        json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    return candidate, digest


def _load_authority_consumer(
    source_root: Path, *, authority_home: Path | None = None
) -> types.ModuleType:
    """Execute one byte-bound snapshot of the externally anchored consumer."""

    path = _source_file(
        source_root,
        AUTHORITY_CONSUMER_PATH,
        label="Hermes runtime provenance authority",
    )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail("Hermes runtime provenance authority unavailable")
    try:
        before = os.fstat(descriptor)
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    source = b"".join(chunks)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        or hashlib.sha256(source).hexdigest() != AUTHORITY_CONSUMER_SHA256
    ):
        _fail("Hermes runtime provenance authority unavailable")
    module_name = "tui_gateway.maestro_authority"
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = "tui_gateway"
    module.__spec__ = None
    if authority_home is not None:
        module.__dict__["_ORCH_PROTECTED_AUTHORITY_HOME_ROUTE"] = authority_home
    try:
        code = compile(source, str(path), "exec", dont_inherit=True)
        sys.modules[module_name] = module
        exec(code, module.__dict__)
    except BaseException:
        sys.modules.pop(module_name, None)
        _fail("Hermes runtime provenance authority unavailable")
    return module


def _consume_runtime_provenance_authority(
    manifest_value: object,
    *,
    source_root: Path | None = None,
    authority_home: Path | None = None,
) -> tuple[dict, str]:
    """Consume one signed current tuple before any operational MCP execution."""

    candidate, candidate_digest = _candidate_runtime_provenance(manifest_value)
    consumer_args = {}
    if authority_home is not None:
        consumer_args["authority_home"] = authority_home
    maestro_authority = _load_authority_consumer(
        REPO_ROOT if source_root is None else source_root,
        **consumer_args,
    )
    if (
        authority_home is not None
        and maestro_authority._PROTECTED_AUTHORITY_HOME != authority_home
    ):
        _fail("Hermes runtime provenance authority unavailable")
    if (
        maestro_authority.HERMES_MAESTRO_AUTHORITY_BUNDLE_DIGEST
        != AUTHORITY_BUNDLE_DIGEST
    ):
        _fail("Hermes runtime provenance authority unavailable")
    now = time.time()
    decision_id = f"hermes-mcp-startup-{os.urandom(16).hex()}"
    logical_session_id = "orch-next-hermes-mcp-startup"
    context = {
        "contract_version": maestro_authority.HERMES_OPERATIONAL_CONTEXT_VERSION,
        "authority_bundle": {
            "identity": maestro_authority.HERMES_MAESTRO_AUTHORITY_BUNDLE_ID,
            "version": maestro_authority.HERMES_MAESTRO_AUTHORITY_BUNDLE_VERSION,
            "digest": AUTHORITY_BUNDLE_DIGEST,
        },
        "threshold_policy": {
            "version": maestro_authority.HERMES_TELEMETRY_SCHEMA_VERSION,
            "digest": maestro_authority.HERMES_TELEMETRY_SCHEMA_DIGEST,
        },
        "decision_binding": {
            "decision_id": decision_id,
            "requester": maestro_authority.HERMES_AUTHORITY_CONSUMER,
            "account_id": "orch-next-runtime",
            "project_id": "hermes-exclusive-harness",
            "logical_session_id": logical_session_id,
            "method": maestro_authority.HERMES_OPERATIONAL_METHOD,
            "target": maestro_authority.HERMES_OPERATIONAL_TARGET,
            "runtime_revision": candidate["runtimeCommit"],
        },
        "goal": maestro_authority.HERMES_OPERATIONAL_GOAL,
        "operation": maestro_authority.HERMES_OPERATIONAL_METHOD,
        "target": maestro_authority.HERMES_OPERATIONAL_TARGET,
        "revision": maestro_authority.HERMES_OPERATIONAL_REVISION,
        "issued_at": now,
        "expires_at": now + 60,
        "operation_id": decision_id,
        "task_declaration": {
            "task_class": "operations",
            "prompt_contract_version": (
                maestro_authority.HERMES_SESSION_TOKEN_PROMPT_CONTRACT_VERSION
            ),
            "prompt_contract_digest": (
                maestro_authority.HERMES_SESSION_TOKEN_PROMPT_CONTRACT_DIGEST
            ),
        },
    }
    actual = {
        "logical_session_id": logical_session_id,
        "ui_session_id": PLUGIN_ID,
        "method": maestro_authority.HERMES_OPERATIONAL_METHOD,
        "target": maestro_authority.HERMES_OPERATIONAL_TARGET,
        "runtime_revision": candidate["runtimeCommit"],
    }
    result = maestro_authority.consume_maestro_authority_decision(context, actual)
    expected_keys = {
        "outcome",
        "decision_id",
        "consumed_once",
        "runtime_provenance_manifest",
        "runtime_provenance_manifest_digest",
    }
    if (
        type(result) is not dict
        or set(result) != expected_keys
        or result.get("outcome") != "allow"
        or result.get("decision_id") != decision_id
        or result.get("consumed_once") is not True
        or result.get("runtime_provenance_manifest") != candidate
        or result.get("runtime_provenance_manifest_digest") != candidate_digest
    ):
        _fail("Hermes runtime provenance authority unavailable")
    return candidate, candidate_digest


def _acquire_lifecycle_source_lock(bundle_root: Path) -> tuple[int, Path]:
    """Join the distribution generator's existing exactly-one-writer protocol."""

    lock_path = bundle_root.parent / f".{bundle_root.name}.distribution.lock"
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.set_inheritable(descriptor, True)
    except OSError:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                lock_path.unlink()
            except OSError:
                pass
        _fail("Hermes lifecycle source lock unavailable")
    return descriptor, lock_path


def _admit_lifecycle_authority_home(service_args: list[str]) -> Path:
    """Admit one canonical profile route before lifecycle authority consumption."""

    positions = [
        index for index, value in enumerate(service_args) if value == "--hermes-home"
    ]
    if (
        len(positions) != 1
        or any(value.startswith("--hermes-home=") for value in service_args)
        or positions[0] + 1 >= len(service_args)
    ):
        _fail("Hermes lifecycle authority home unavailable")
    raw = service_args[positions[0] + 1]
    if not raw or raw.startswith("-"):
        _fail("Hermes lifecycle authority home unavailable")
    home = Path(raw)
    try:
        resolved_home = home.resolve(strict=True)
        home_info = os.lstat(home)
    except OSError:
        _fail("Hermes lifecycle authority home unavailable")
    if (
        not home.is_absolute()
        or home != resolved_home
        or stat.S_ISLNK(home_info.st_mode)
        or not stat.S_ISDIR(home_info.st_mode)
        or home_info.st_uid != os.getuid()
        or stat.S_IMODE(home_info.st_mode) & 0o077
    ):
        _fail("Hermes lifecycle authority home unavailable")
    return home


def _release_lifecycle_source_lock(descriptor: int, lock_path: Path) -> None:
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = lock_path.lstat()
        if (
            descriptor_stat.st_dev == path_stat.st_dev
            and descriptor_stat.st_ino == path_stat.st_ino
        ):
            lock_path.unlink()
    except OSError:
        pass
    try:
        os.close(descriptor)
    except OSError:
        pass


def _runtime_file_digest(bundle_root: Path, relative_path: str) -> str:
    binding = _json_object(
        bundle_root / "runtime" / RUNTIME_BINDING_NAME,
        label="runtime locator binding",
    )
    entries = binding.get("runtime_files")
    if type(entries) is not list:
        _fail("runtime locator content binding is unavailable")
    matches = [
        entry.get("sha256")
        for entry in entries
        if type(entry) is dict and entry.get("path") == relative_path
    ]
    if len(matches) != 1 or type(matches[0]) is not str or len(matches[0]) != 64:
        _fail("runtime locator content binding is unavailable")
    return matches[0]


def _open_verified_lifecycle_service(service_path: Path, expected_digest: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(service_path, flags)
        before = os.fstat(descriptor)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.set_inheritable(descriptor, True)
    except OSError:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        _fail("lifecycle service snapshot unavailable")
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        or digest.hexdigest() != expected_digest
    ):
        os.close(descriptor)
        _fail("lifecycle service snapshot unavailable")
    return descriptor


def _execute_lifecycle_service_snapshot(
    service_path: Path,
    descriptor: int,
    expected_digest: str,
    lock_descriptor: int,
    service_args: list[str],
) -> None:
    """Execute exact service bytes in the already trusted isolated controller."""

    before = os.fstat(descriptor)
    chunks = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    after = os.fstat(descriptor)
    os.close(descriptor)
    source = b"".join(chunks)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        or hashlib.sha256(source).hexdigest() != expected_digest
    ):
        _fail("lifecycle service snapshot unavailable")
    code = compile(source, str(service_path), "exec", dont_inherit=True)
    sys.argv = [str(service_path), *service_args]
    os.environ.clear()
    os.environ.update({
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        LIFECYCLE_SOURCE_LOCK_ENV: str(lock_descriptor),
    })
    namespace = {
        "__name__": "__main__",
        "__file__": str(service_path),
        "__package__": None,
        "__spec__": None,
    }
    exec(code, namespace)


def _run_portable_wrapper(bundle_root: Path) -> None:
    runtime_python, runtime_python_target, runtime_launcher = _verify_binding(
        bundle_root
    )
    argv = [str(runtime_python), "-I", str(runtime_launcher), *sys.argv[1:]]
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    if sys.argv[1:2] == [LIFECYCLE_SERVICE_FLAG]:
        argv = [
            SYSTEM_PYTHON,
            "-I",
            "-S",
            str(runtime_launcher),
            RUNTIME_PROVENANCE_MANIFEST_FLAG,
            str((bundle_root / SOURCE_MANIFEST_NAME).resolve(strict=True)),
            *sys.argv[1:],
        ]
        os.execve(SYSTEM_PYTHON, argv, env)
    if sys.argv[1:] == ["--verify-origin"]:
        completed = subprocess.run(
            [*argv[:-1], RUNTIME_ORIGIN_PROBE_FLAG],
            executable=runtime_python_target,
            cwd=Path("/private/tmp"),
            env=env,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        if completed.returncode != 0:
            _fail("admitted Hermes runtime origin probe failed")
        return
    # Re-resolve and re-hash immediately before exec so a venv-link retarget
    # after admission cannot select a different interpreter.
    runtime_python, runtime_python_target, runtime_launcher = _verify_binding(
        bundle_root
    )
    argv = [
        str(runtime_python),
        "-I",
        str(runtime_launcher),
        RUNTIME_PROVENANCE_MANIFEST_FLAG,
        str((bundle_root / SOURCE_MANIFEST_NAME).resolve(strict=True)),
        *sys.argv[1:],
    ]
    os.execve(runtime_python_target, argv, env)


def _run_lifecycle_service(
    *,
    bundle_root: Path | None = None,
    service_args: list[str] | None = None,
) -> None:
    """Bind external authority, then execute one post-authority service snapshot."""

    if service_args is None:
        service_args = sys.argv[2:]
    authority_home = _admit_lifecycle_authority_home(service_args)
    if bundle_root is None:
        source_root = REPO_ROOT.resolve(strict=True)
        bundle_root = (source_root / "distribution" / PLUGIN_ID).resolve(strict=True)
    else:
        bundle_root = bundle_root.resolve(strict=True)
        binding = _json_object(
            bundle_root / "runtime" / RUNTIME_BINDING_NAME,
            label="runtime locator binding",
        )
        source_root = _source_root(bundle_root, binding)
    runtime_dir = (bundle_root / "runtime").resolve(strict=True)

    def verify() -> tuple[Path, Path, Path]:
        return _verify_binding(
            bundle_root,
            runtime_dir=runtime_dir,
            expected_source_root=source_root,
        )

    verify()
    source_bundle_root = source_root / "distribution" / PLUGIN_ID
    manifest_path = (source_bundle_root / SOURCE_MANIFEST_NAME).resolve(strict=True)
    admitted_candidate, _candidate_digest = _consume_runtime_provenance_authority(
        str(manifest_path),
        source_root=source_root,
        authority_home=authority_home,
    )
    lock_descriptor, lock_path = _acquire_lifecycle_source_lock(source_bundle_root)
    service_descriptor = -1
    try:
        verify()
        if _sha256(manifest_path) != admitted_candidate["runtimeContentDigest"]:
            _fail("Hermes runtime provenance changed after authority admission")
        service_path = _source_file(
            source_root,
            LIFECYCLE_SERVICE_PATH,
            label="lifecycle service",
            executable=True,
        )
        service_digest = _runtime_file_digest(bundle_root, LIFECYCLE_SERVICE_PATH)
        service_descriptor = _open_verified_lifecycle_service(
            service_path, service_digest
        )
        # The user-managed venv interpreter is still part of the admitted
        # runtime identity, but is never reopened for lifecycle execution.
        # Continue in the fixed /usr/bin/python3 -I -S controller process that
        # the official shell already established, and reverify the complete
        # closure immediately before consuming the service snapshot.
        verify()
    except BaseException:
        if service_descriptor >= 0:
            os.close(service_descriptor)
        _release_lifecycle_source_lock(lock_descriptor, lock_path)
        raise
    try:
        _execute_lifecycle_service_snapshot(
            service_path,
            service_descriptor,
            service_digest,
            lock_descriptor,
            service_args,
        )
    finally:
        try:
            os.close(service_descriptor)
        except OSError:
            pass
        _release_lifecycle_source_lock(lock_descriptor, lock_path)


def main() -> None:
    if sys.argv[1:] == [RUNTIME_ORIGIN_PROBE_FLAG]:
        verified_origin()
        return
    bundle_root = _bundle_root()
    if bundle_root is not None:
        _run_portable_wrapper(bundle_root)
        return
    if sys.argv[1:2] == [LIFECYCLE_SERVICE_FLAG]:
        _run_lifecycle_service()
        return
    if sys.argv[1:] == ["--verify-origin"]:
        verified_startup()
        return
    if len(sys.argv) < 3 or sys.argv[1] != RUNTIME_PROVENANCE_MANIFEST_FLAG:
        _fail("Hermes runtime provenance authority unavailable")
    manifest_path = Path(sys.argv[2]).resolve(strict=True)
    if sys.argv[3:4] == [LIFECYCLE_SERVICE_FLAG]:
        _run_lifecycle_service(
            bundle_root=manifest_path.parent,
            service_args=sys.argv[4:],
        )
        return
    verified_origin()
    _consume_runtime_provenance_authority(sys.argv[2])
    sys.argv = [sys.argv[0], *sys.argv[3:]]
    runpy.run_module(MCP_MODULE, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
