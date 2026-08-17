#!/usr/bin/env python3
"""Install, verify and manage the Codex -> DeepSeek dual-agent router.

Adapted from oil-oil/codex-deepseek-subagent (MIT), scripts/codex_deepseek.py.
See docs/upstream-reference-map.md for the per-symbol source map.

The router installs two native Codex child agents side by side:

    deepseek_flash -> deepseek-v4-flash   (fast bounded worker)
    deepseek_pro   -> deepseek-v4-pro     (deep solver / reviewer)

The Codex parent model and provider are never changed. The DeepSeek provider
is declared inside each agent TOML (env_key on Windows/Linux, macOS Keychain
command auth on macOS), and cross-provider task delivery uses the plaintext
SubagentStart handoff transport (hooks/plaintext_handoff.py).
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # macOS / Linux
    msvcrt = None

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - depends on interpreter version
    tomllib = None

try:
    import tomli  # Optional backport
except ImportError:
    tomli = None

try:
    from enum import StrEnum  # Python 3.11+
except ImportError:  # Python < 3.11 - str-mixin shim with identical behavior
    from enum import Enum as _Enum

    class StrEnum(str, _Enum):
        def __str__(self) -> str:
            return str(self.value)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_NAME = "codex-deepseek-router"
FLASH_ROLE = "deepseek_flash"
PRO_ROLE = "deepseek_pro"
FLASH_MODEL = "deepseek-v4-flash"
PRO_MODEL = "deepseek-v4-pro"
PROVIDER = "deepseek"
BASE_URL = "https://api.deepseek.com"
WIRE_API = "responses"
HASH_VERSION_EXACT_BYTES = 2
API_KEY_ENV = "DEEPSEEK_API_KEY"

SUPPORTED_ROLES = {
    FLASH_ROLE: FLASH_MODEL,
    PRO_ROLE: PRO_MODEL,
}
VALID_AGENTS = set(SUPPORTED_ROLES)
POLICIES = ("FAST", "REACT", "SPEC", "DEEP")

CREDENTIAL_TARGET = "io.github.codex-deepseek-router.deepseek-api-key"
HOOKS_INSTALL_DIR_NAME = PROJECT_NAME
MAX_STATE_DATABASES = 32
METADATA_WAIT_SECONDS = 5.0
LOCK_WAIT_SECONDS = 5.0
MAX_ASSIGNMENT_CHARS = 1_000_000

DESKTOP_CODEX_CANDIDATES = (
    Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
    Path("/Applications/Codex.app/Contents/Resources/codex"),
)
WINDOWS_CODEX_RELATIVE_CANDIDATES = (
    Path("Programs") / "Codex" / "resources" / "codex.exe",
    Path("Programs") / "OpenAI" / "Codex" / "resources" / "codex.exe",
    Path("Codex") / "resources" / "codex.exe",
)


class Modality(StrEnum):
    TEXT_ONLY = "TEXT_ONLY"
    VISION_TRANSLATABLE = "VISION_TRANSLATABLE"
    VISION_CRITICAL = "VISION_CRITICAL"


class TransportMode(StrEnum):
    NATIVE = "native"
    PLAINTEXT_HOOK = "plaintext_hook"
    LEGACY_V1 = "legacy_v1"


def deepseek_allowed(modality: Optional[str]) -> bool:
    return modality in {
        Modality.TEXT_ONLY,
        Modality.VISION_TRANSLATABLE,
    }


def choose_transport(native_probe_ok: bool, hook_available: bool) -> TransportMode:
    if native_probe_ok:
        return TransportMode.NATIVE
    if hook_available:
        return TransportMode.PLAINTEXT_HOOK
    return TransportMode.LEGACY_V1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ManagerError(RuntimeError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class HandoffBusy(RuntimeError):
    """A handoff slot for this agent role is already occupied."""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Paths:
    codex_home: Path

    @property
    def config(self) -> Path:
        return self.codex_home / "config.toml"

    @property
    def catalog(self) -> Path:
        return self.codex_home / "models.json"

    @property
    def agents_dir(self) -> Path:
        return self.codex_home / "agents"

    @property
    def flash_agent(self) -> Path:
        return self.agents_dir / "deepseek-flash.toml"

    @property
    def pro_agent(self) -> Path:
        return self.agents_dir / "deepseek-pro.toml"

    @property
    def state_dir(self) -> Path:
        return self.codex_home / "deepseek-router"

    @property
    def manifest(self) -> Path:
        return self.state_dir / "manifest.json"

    @property
    def handoff_dir(self) -> Path:
        return self.state_dir / "handoff"

    @property
    def backups_dir(self) -> Path:
        return self.state_dir / "backups"

    @property
    def hooks_config(self) -> Path:
        return self.codex_home / "hooks.json"

    @property
    def hooks_install_dir(self) -> Path:
        return self.codex_home / "hooks" / HOOKS_INSTALL_DIR_NAME

    @property
    def runtime_skill_dir(self) -> Path:
        return self.codex_home / "skills" / "use-deepseek-router"

    def agent_path(self, agent_type: str) -> Path:
        if agent_type == FLASH_ROLE:
            return self.flash_agent
        if agent_type == PRO_ROLE:
            return self.pro_agent
        raise ValueError(f"unknown agent type: {agent_type}")


@dataclass(frozen=True)
class AgentSpec:
    role: str
    model: str
    description: str
    sandbox_mode: str


AGENT_SPECS = {
    FLASH_ROLE: AgentSpec(
        role=FLASH_ROLE,
        model=FLASH_MODEL,
        description=(
            "Fast text-only read-only DeepSeek worker for bounded exploration, "
            "search, logs, extraction and pre-implementation analysis."
        ),
        sandbox_mode="read-only",
    ),
    PRO_ROLE: AgentSpec(
        role=PRO_ROLE,
        model=PRO_MODEL,
        description=(
            "Deep text-only DeepSeek solver for root cause, architecture, "
            "concurrency, review and difficult implementation."
        ),
        sandbox_mode="workspace-write",
    ),
}


@dataclass(frozen=True)
class RoutingDecision:
    agent: str  # NONE | FLASH | PRO
    policy: str  # FAST | REACT | SPEC | DEEP
    modality: str
    reason: str


@dataclass
class VisualContext:
    source_type: str
    user_goal: str
    observations: List[str] = field(default_factory=list)
    visible_text: List[str] = field(default_factory=list)
    relationships: List[str] = field(default_factory=list)
    uncertainties: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": 1,
            "source_type": self.source_type,
            "user_goal": self.user_goal,
            "observations": self.observations,
            "visible_text": self.visible_text,
            "relationships": self.relationships,
            "uncertainties": self.uncertainties,
            "source_visibility": "parent_only",
        }


@dataclass
class EvidencePacket:
    summary: str
    relevant_files: List[str]
    observations: List[str]
    hypotheses: List[str]
    eliminated: List[str]
    open_questions: List[str]
    recommended_next_step: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": 1,
            "summary": self.summary,
            "relevant_files": self.relevant_files,
            "observations": self.observations,
            "hypotheses": self.hypotheses,
            "eliminated": self.eliminated,
            "open_questions": self.open_questions,
            "recommended_next_step": self.recommended_next_step,
        }


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def result(status: str, **kwargs: Any) -> Dict[str, Any]:
    return {"status": status, **kwargs}


def emit(payload: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(payload.get("status", "unknown"))
    for key, value in payload.items():
        if key != "status":
            print(f"{key}: {value}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text_file(path: Path) -> str:
    normalized = path.read_text().replace("\r\n", "\n").replace("\r", "\n")
    return sha256_bytes(normalized.encode())


def sha256_file(path: Path) -> str:
    """Hash exact bytes for ownership and transaction integrity checks."""
    return sha256_bytes(path.read_bytes())


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def platform_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt" or sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return "unsupported"


def resolve_paths(codex_home: Optional[str]) -> Paths:
    home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return Paths(home.expanduser().resolve())


def package_root() -> Path:
    """The installed package dir (the one containing scripts/, agents/, skills/)."""
    return Path(__file__).resolve().parents[1]


def repo_root() -> Path:
    """The repository checkout root when running from a source checkout."""
    return Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Minimal TOML access (stdlib-only, Python 3.9 compatible)
# ---------------------------------------------------------------------------


def load_toml(text: str) -> Dict[str, Any]:
    if tomllib is not None:
        return tomllib.loads(text)
    if tomli is not None:
        return tomli.loads(text)
    raise ManagerError(
        "toml_parser_missing",
        "No TOML parser is available in this Python runtime (3.11+ or tomli required).",
    )


def toml_top_level_string(text: str, key: str) -> Optional[str]:
    """Best-effort top-level `key = "value"` extraction without a full parser.

    Used only for reading the parent model/provider from config.toml on
    Python runtimes that have neither tomllib nor tomli. The regex only
    matches simple single-line string assignments before the first table
    header, which is exactly the shape Codex writes for these keys.
    """
    pattern = re.compile(
        r'^\s*' + re.escape(key) + r'\s*=\s*"((?:[^"\\]|\\.)*)"\s*(?:#.*)?$'
    )
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            break
        match = pattern.match(line)
        if match:
            return match.group(1).encode().decode("unicode_escape")
    return None


def toml_get_top_level_string(text: str, key: str) -> Optional[str]:
    if not text.strip():
        return None
    if tomllib is not None or tomli is not None:
        try:
            value = load_toml(text).get(key)
            return value if isinstance(value, str) else None
        except ManagerError:
            return None
    return toml_top_level_string(text, key)


def toml_has_table(text: str, table: str) -> bool:
    """True when a `[table]` section (or a dotted sub-table) exists."""
    tokens = [
        r'(?:' + re.escape(part) + r'|"' + re.escape(part) + r'"|' + re.escape("'" + part + "'") + r')'
        for part in table.split(".")
    ]
    pattern = re.compile(r"^\[\s*" + r"\s*\.\s*".join(tokens) + r"\s*(\]|\.)")
    return any(pattern.match(line.strip()) for line in text.splitlines())


# ---------------------------------------------------------------------------
# Codex runtime discovery (ported from oil-oil)
# ---------------------------------------------------------------------------


def find_desktop_codex() -> str:
    configured = os.environ.get("CODEX_DESKTOP_BIN")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        raise ManagerError(
            "desktop_codex_missing",
            f"CODEX_DESKTOP_BIN points to a missing file: {candidate}",
        )

    candidates: List[Path] = []
    platform = platform_name()
    if platform == "macos":
        candidates.extend(DESKTOP_CODEX_CANDIDATES)
    elif platform == "windows":
        for variable in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            root = os.environ.get(variable)
            if root:
                candidates.extend(
                    Path(root) / relative for relative in WINDOWS_CODEX_RELATIVE_CANDIDATES
                )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())

    discovered = shutil.which("codex.exe") if platform == "windows" else shutil.which("codex")
    if discovered:
        return discovered

    raise ManagerError(
        "desktop_codex_missing",
        "Could not find the Codex runtime. Install or start the Codex desktop app, "
        "or set CODEX_DESKTOP_BIN to the bundled codex binary.",
    )


def codex_version_text(codex_bin: str) -> str:
    proc = subprocess.run(
        [codex_bin, "--version"], capture_output=True, text=True, timeout=15
    )
    text = f"{proc.stdout}\n{proc.stderr}".strip()
    if proc.returncode != 0 or not text:
        raise ManagerError("codex_version_unknown", "Could not read the Codex runtime version.")
    return text


# ---------------------------------------------------------------------------
# Credentials (ported from oil-oil, retargeted credential name)
# ---------------------------------------------------------------------------


def credential_account() -> str:
    return getpass.getuser()


def credential_backend() -> Optional[str]:
    platform = platform_name()
    if platform == "macos" and Path("/usr/bin/security").is_file():
        return "macos-keychain"
    if platform == "windows":
        return "windows-credential-manager"
    return None


def credential_present() -> bool:
    if os.environ.get(API_KEY_ENV):
        return True
    backend = credential_backend()
    if backend is None:
        # Linux V1: environment variable only.
        return False
    try:
        return read_credential_key() is not None
    except ManagerError:
        return False


def _macos_read_credential() -> Optional[str]:
    proc = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            credential_account(),
            "-s",
            CREDENTIAL_TARGET,
            "-w",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


_K_CF_STRING_ENCODING_UTF8 = 0x08000100
_ERR_SEC_SUCCESS = 0
_ERR_SEC_DUPLICATE_ITEM = -25299


def _macos_security_framework():
    """Load Security.framework / CoreFoundation via ctypes.

    Used for the credential write path so the secret never enters argv (the
    `security` CLI's `-w` option would, and Apple marks that usage unsafe).
    """
    import ctypes
    from ctypes import c_void_p

    security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    core_foundation = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

    security.SecItemAdd.argtypes = [c_void_p, ctypes.POINTER(c_void_p)]
    security.SecItemAdd.restype = ctypes.c_int32
    security.SecItemUpdate.argtypes = [c_void_p, c_void_p]
    security.SecItemUpdate.restype = ctypes.c_int32
    security.SecItemDelete.argtypes = [c_void_p]
    security.SecItemDelete.restype = ctypes.c_int32

    core_foundation.CFStringCreateWithCString.argtypes = [c_void_p, ctypes.c_char_p, ctypes.c_int32]
    core_foundation.CFStringCreateWithCString.restype = c_void_p
    core_foundation.CFDataCreate.argtypes = [c_void_p, c_void_p, ctypes.c_long]
    core_foundation.CFDataCreate.restype = c_void_p
    core_foundation.CFDictionaryCreate.argtypes = [
        c_void_p,
        ctypes.POINTER(c_void_p),
        ctypes.POINTER(c_void_p),
        ctypes.c_long,
        c_void_p,
        c_void_p,
    ]
    core_foundation.CFDictionaryCreate.restype = c_void_p
    core_foundation.CFRelease.argtypes = [c_void_p]
    core_foundation.CFRelease.restype = None
    return security, core_foundation, ctypes


def _macos_security_constants(security, core_foundation, ctypes) -> Dict[str, int]:
    """Resolve Security/CoreFoundation constants as their exported pointers."""

    def exported_pointer(library, name: str) -> int:
        value = ctypes.c_void_p.in_dll(library, name).value
        if value is None:
            raise ValueError(f"macOS framework symbol {name} was null")
        return value

    def exported_struct(library, name: str) -> int:
        return ctypes.addressof(ctypes.c_byte.in_dll(library, name))

    return {
        "class": exported_pointer(security, "kSecClass"),
        "generic_password": exported_pointer(security, "kSecClassGenericPassword"),
        "service": exported_pointer(security, "kSecAttrService"),
        "account": exported_pointer(security, "kSecAttrAccount"),
        "value_data": exported_pointer(security, "kSecValueData"),
        "key_callbacks": exported_struct(core_foundation, "kCFTypeDictionaryKeyCallBacks"),
        "value_callbacks": exported_struct(core_foundation, "kCFTypeDictionaryValueCallBacks"),
    }


def _macos_store_credential(secret: str) -> None:
    """Store the API key through SecItemAdd. The secret never enters argv."""
    try:
        security, cf, ctypes = _macos_security_framework()
        constants = _macos_security_constants(security, cf, ctypes)
    except (OSError, ValueError) as exc:
        raise ManagerError(
            "credential_write_failed",
            f"Could not load Security.framework: {exc}",
        ) from exc

    def cf_string(value: str):
        return cf.CFStringCreateWithCString(None, value.encode("utf-8"), _K_CF_STRING_ENCODING_UTF8)

    def cf_data(value: bytes):
        buffer = ctypes.create_string_buffer(value)
        return cf.CFDataCreate(None, buffer, len(value))

    def dictionary(entries):
        keys = (ctypes.c_void_p * len(entries))(*[key for key, _ in entries])
        values = (ctypes.c_void_p * len(entries))(*[value for _, value in entries])
        return cf.CFDictionaryCreate(
            None,
            keys,
            values,
            len(entries),
            constants["key_callbacks"],
            constants["value_callbacks"],
        )

    with ExitStack() as cleanup:
        def owned(ref, label: str):
            if not ref:
                raise ManagerError(
                    "credential_write_failed",
                    f"CoreFoundation could not allocate {label}.",
                )
            cleanup.callback(cf.CFRelease, ref)
            return ref

        target_ref = owned(cf_string(CREDENTIAL_TARGET), "the Keychain service string")
        user_ref = owned(cf_string(credential_account()), "the Keychain account string")
        secret_ref = owned(cf_data(secret.encode("utf-8")), "the credential data")
        query = owned(
            dictionary(
                [
                    (constants["class"], constants["generic_password"]),
                    (constants["service"], target_ref),
                    (constants["account"], user_ref),
                ]
            ),
            "the Keychain query",
        )
        item = owned(
            dictionary(
                [
                    (constants["class"], constants["generic_password"]),
                    (constants["service"], target_ref),
                    (constants["account"], user_ref),
                    (constants["value_data"], secret_ref),
                ]
            ),
            "the Keychain item",
        )
        status = security.SecItemAdd(item, None)
        if status == _ERR_SEC_DUPLICATE_ITEM:
            updates = owned(
                dictionary([(constants["value_data"], secret_ref)]),
                "the Keychain update",
            )
            status = security.SecItemUpdate(query, updates)
        if status != _ERR_SEC_SUCCESS:
            raise ManagerError(
                "credential_write_failed",
                f"Keychain credential write failed (status {status}).",
            )


def _macos_remove_credential() -> bool:
    proc = subprocess.run(
        ["/usr/bin/security", "delete-generic-password", "-a", credential_account(), "-s", CREDENTIAL_TARGET],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def _windows_credential_api():
    import ctypes
    from ctypes import wintypes

    class CredentialW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CredentialW)),
    ]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(CredentialW), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredFree.restype = None
    return ctypes, CredentialW, advapi32


def _windows_read_credential() -> Optional[str]:
    ctypes, credential_type, advapi32 = _windows_credential_api()
    credential = ctypes.POINTER(credential_type)()
    if not advapi32.CredReadW(CREDENTIAL_TARGET, 1, 0, ctypes.byref(credential)):
        error = ctypes.get_last_error()
        if error == 1168:
            return None
        raise ManagerError(
            "credential_read_failed",
            f"Could not read Windows Credential Manager (error {error}).",
        )
    try:
        raw = ctypes.string_at(
            credential.contents.CredentialBlob,
            credential.contents.CredentialBlobSize,
        )
        return raw.decode("utf-8")
    finally:
        advapi32.CredFree(credential)


def _windows_store_credential(secret: str) -> None:
    ctypes, credential_type, advapi32 = _windows_credential_api()
    raw = secret.encode("utf-8")
    blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    credential = credential_type()
    credential.Flags = 0
    credential.Type = 1
    credential.TargetName = CREDENTIAL_TARGET
    credential.CredentialBlobSize = len(raw)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2
    credential.UserName = credential_account()
    if not advapi32.CredWriteW(ctypes.byref(credential), 0):
        error = ctypes.get_last_error()
        raise ManagerError(
            "credential_write_failed",
            f"Could not write the API key to Windows Credential Manager (error {error}).",
        )


def _windows_remove_credential() -> bool:
    ctypes, _, advapi32 = _windows_credential_api()
    if advapi32.CredDeleteW(CREDENTIAL_TARGET, 1, 0):
        return True
    error = ctypes.get_last_error()
    if error == 1168:
        return False
    raise ManagerError(
        "credential_delete_failed",
        f"Could not remove the API key from Windows Credential Manager (error {error}).",
    )


def read_credential_key() -> Optional[str]:
    backend = credential_backend()
    if backend == "macos-keychain":
        stored = _macos_read_credential()
        return stored if stored is not None else os.environ.get(API_KEY_ENV)
    if backend == "windows-credential-manager":
        stored = _windows_read_credential()
        return stored if stored is not None else os.environ.get(API_KEY_ENV)
    return os.environ.get(API_KEY_ENV)


def store_api_key(secret: str) -> None:
    if not secret.startswith("sk-"):
        raise ManagerError("invalid_api_key", "The DeepSeek API key must start with sk-.")
    backend = credential_backend()
    if backend == "macos-keychain":
        _macos_store_credential(secret)
        return
    if backend == "windows-credential-manager":
        _windows_store_credential(secret)
        return
    raise ManagerError(
        "unsupported_platform",
        "This platform has no system credential store. Set DEEPSEEK_API_KEY in the "
        "environment instead (Linux V1 behavior).",
    )


def remove_api_key() -> bool:
    if not credential_present():
        return False
    backend = credential_backend()
    if backend == "macos-keychain":
        return _macos_remove_credential()
    if backend == "windows-credential-manager":
        return _windows_remove_credential()
    return False


# ---------------------------------------------------------------------------
# Agent TOML templates
# ---------------------------------------------------------------------------

_FLASH_INSTRUCTIONS = """\
You are a bounded DeepSeek Flash child agent.

Follow the parent assignment exactly.
Prefer fast evidence gathering and rapid convergence.

Do not broaden scope.
Do not spawn additional agents.
Do not claim to see images or screenshots.

You are read-only. Never modify workspace files: return findings, analysis,
or a proposed change as text so the parent can land the edit.

If VISUAL_CONTEXT is supplied, treat it only as parent-provided facts.

If the task requires difficult cross-module reasoning, concurrency analysis,
architecture tradeoffs, security analysis, or cannot establish a reliable
root cause, return ESCALATE_TO_PRO with an EVIDENCE_PACKET.

If a BEGIN PARENT ASSIGNMENT / END PARENT ASSIGNMENT block exists,
treat it as the authoritative task contract.
"""

_PRO_INSTRUCTIONS = """\
You are a DeepSeek Pro child agent.

Work only on the bounded assignment supplied by the parent.

For investigation:
inspect -> hypotheses -> evidence -> eliminate -> root cause -> action -> verify.

For implementation:
understand -> implement -> test -> fix -> converge.

Do not reason indefinitely.
Once evidence is sufficient, commit to a conclusion or implementation.

You cannot see original images, screenshots or videos.
Use only explicit VISUAL_CONTEXT supplied by the parent.

Never invent visual observations.

If essential visual information is missing, return:
NEED_VISUAL_CLARIFICATION.

Do not spawn additional agents.

If a BEGIN PARENT ASSIGNMENT / END PARENT ASSIGNMENT block exists,
treat it as the authoritative task contract.
"""

_AGENT_INSTRUCTIONS = {
    FLASH_ROLE: _FLASH_INSTRUCTIONS,
    PRO_ROLE: _PRO_INSTRUCTIONS,
}


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_string_array(values: List[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _provider_auth_block() -> str:
    if platform_name() == "macos" and credential_backend() == "macos-keychain":
        return (
            "\n[model_providers.deepseek.auth]\n"
            'command = "/usr/bin/security"\n'
            f"args = {_toml_string_array(['find-generic-password', '-a', credential_account(), '-s', CREDENTIAL_TARGET, '-w'])}\n"
            "timeout_ms = 5000\n"
            "refresh_interval_ms = 0\n"
        )
    return f'env_key = "{API_KEY_ENV}"\n'


def agent_toml_text(spec: AgentSpec) -> str:
    instructions = _AGENT_INSTRUCTIONS[spec.role].strip()
    return (
        f'name = "{spec.role}"\n'
        f"description = {_toml_string(spec.description)}\n\n"
        f'model_provider = "{PROVIDER}"\n'
        f'model = "{spec.model}"\n'
        f"model_context_window = 1000000\n"
        f'sandbox_mode = "{spec.sandbox_mode}"\n\n'
        f"developer_instructions = {_toml_string(instructions)}\n\n"
        f"[model_providers.{PROVIDER}]\n"
        f'name = "DeepSeek"\n'
        f'base_url = "{BASE_URL}"\n'
        f'wire_api = "{WIRE_API}"\n'
        + _provider_auth_block()
    )


# ---------------------------------------------------------------------------
# Model catalog (dual registration, never one without the other)
# ---------------------------------------------------------------------------


def catalog_payload() -> Dict[str, Any]:
    def entry(model: str, label: str, description: str) -> Dict[str, Any]:
        return {
            "slug": model,
            "name": label,
            "description": description,
            "model_provider": PROVIDER,
            "base_url": BASE_URL,
            "wire_api": WIRE_API,
            "context_window": 1000000,
            "router_roles": [role for role, candidate in SUPPORTED_ROLES.items() if candidate == model],
        }

    return {
        "models": [
            entry(
                FLASH_MODEL,
                "DeepSeek V4 Flash",
                "Fast text-only DeepSeek model for bounded exploration, search, logs, extraction and pre-implementation analysis.",
            ),
            entry(
                PRO_MODEL,
                "DeepSeek V4 Pro",
                "Deep text-only DeepSeek model for root cause, architecture, concurrency, review and difficult implementation.",
            ),
        ]
    }


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def read_manifest(paths: Paths) -> Dict[str, Any]:
    if not paths.manifest.is_file():
        return {}
    try:
        payload = json.loads(paths.manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_manifest(paths: Paths, payload: Dict[str, Any]) -> None:
    atomic_write(paths.manifest, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode())


def default_manifest(original_parent_model: str, original_parent_provider: Optional[str]) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "hash_version": HASH_VERSION_EXACT_BYTES,
        "project": PROJECT_NAME,
        "installed_at": datetime.now().isoformat(timespec="seconds"),
        "managed": {
            "flash_agent": True,
            "pro_agent": True,
            "provider": True,  # provider lives inside the managed agent TOMLs
            "catalog": True,
            "hook": True,
            "runtime_skill": True,
        },
        "adopted_existing": {
            "provider": False,
            "catalog": False,
            "hook": False,
        },
        "original": {
            "parent_model": original_parent_model,
            "parent_provider": original_parent_provider,
        },
        "hashes": {},
        "preexisted": {"catalog": False, "hook_config": False},
        "transport_mode": TransportMode.PLAINTEXT_HOOK.value,
        "disabled": False,
        "last_test": None,
    }


# ---------------------------------------------------------------------------
# Backup / transaction (ported from oil-oil)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManagedAsset:
    path: Path
    verify_integrity: bool = True
    hook_runtime: bool = False
    shared: bool = False


def managed_assets(paths: Paths) -> Dict[str, ManagedAsset]:
    """Single registry for all managed paths and their lifecycle semantics."""
    return {
        "flash_agent": ManagedAsset(paths.flash_agent),
        "pro_agent": ManagedAsset(paths.pro_agent),
        "catalog": ManagedAsset(paths.catalog),
        "runtime_skill": ManagedAsset(
            paths.runtime_skill_dir / "SKILL.md",
            hook_runtime=True,
        ),
        "hook_config": ManagedAsset(
            paths.hooks_config,
            verify_integrity=False,
            shared=True,
        ),
        "hook_script_py": ManagedAsset(
            paths.hooks_install_dir / "plaintext_handoff.py",
            hook_runtime=True,
        ),
        "hook_script_ps1": ManagedAsset(
            paths.hooks_install_dir / "plaintext-handoff.ps1",
            hook_runtime=True,
        ),
    }


def managed_asset_paths(paths: Paths) -> Dict[str, Path]:
    return {key: asset.path for key, asset in managed_assets(paths).items()}


def tracked_files(paths: Paths) -> List[Path]:
    """Every file the manager may write; all of them join the transaction snapshot."""
    return [paths.config, paths.manifest, *managed_asset_paths(paths).values()]


def make_backup(paths: Paths) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = paths.backups_dir / stamp
    backup.mkdir(parents=True, exist_ok=False)
    for source in tracked_files(paths):
        if source.is_file():
            shutil.copy2(source, backup / source.name)
    return backup


def restore_backup(paths: Paths, backup: Path) -> None:
    for target in tracked_files(paths):
        source = backup / target.name
        if source.is_file():
            mode = stat.S_IMODE(source.stat().st_mode)
            atomic_write(target, source.read_bytes(), mode=mode)
            shutil.copystat(source, target)
        elif target.is_file():
            target.unlink()


def try_acquire_file_lock(lock_file) -> bool:
    if fcntl is not None:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
    if msvcrt is not None:
        lock_file.seek(0)
        try:
            first_byte = lock_file.read(1)
        except OSError:
            # Windows denies reads when another handle locks this byte.
            return False
        if first_byte == "":
            lock_file.seek(0)
            lock_file.write("\0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    raise ManagerError("unsupported_platform", "No file locking implementation is available.")


def release_file_lock(lock_file) -> None:
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is not None:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def operation_lock(paths: Paths, timeout_seconds: float = LOCK_WAIT_SECONDS):
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    with (paths.state_dir / "manager.lock").open("a+") as lock_file:
        deadline = time.monotonic() + timeout_seconds
        while True:
            if try_acquire_file_lock(lock_file):
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ManagerError(
                    "operation_in_progress",
                    "Another DeepSeek router configuration operation is still running. Try again later.",
                )
            time.sleep(min(0.1, remaining))
        try:
            yield
        finally:
            release_file_lock(lock_file)


# ---------------------------------------------------------------------------
# Foreign config handling
# ---------------------------------------------------------------------------


def _file_is_ours(path: Path, manifest: Dict[str, Any], manifest_hash_key: str) -> bool:
    """True when the file is byte-identical to the version we installed."""
    expected = (manifest.get("hashes") or {}).get(manifest_hash_key)
    if not expected or not path.is_file():
        return False
    hash_version = manifest.get("hash_version")
    if hash_version == HASH_VERSION_EXACT_BYTES:
        return sha256_file(path) == expected
    # Manifests written before hash_version normalized line endings. Accept
    # that legacy ownership proof once so repair can migrate it safely.
    if hash_version is None:
        return sha256_text_file(path) == expected
    return False


def _assert_writable_target(path: Path, expected: bytes, manifest: Dict[str, Any], manifest_hash_key: str) -> bool:
    """Returns True when the target may be written (missing, ours, or adoptable)."""
    if not path.is_file():
        return True
    if _file_is_ours(path, manifest, manifest_hash_key):
        return True
    if sha256_file(path) == sha256_bytes(expected):
        return True  # identical content: adopt
    return False


# ---------------------------------------------------------------------------
# Installers
# ---------------------------------------------------------------------------


def install_agent(paths: Paths, spec: AgentSpec, manifest: Dict[str, Any]) -> bool:
    """Write one agent TOML. Raises conflict on foreign content. Returns True when the file changed."""
    target = paths.agent_path(spec.role)
    text = agent_toml_text(spec)
    data = text.encode()
    hash_key = "flash_agent" if spec.role == FLASH_ROLE else "pro_agent"
    if target.is_file() and not _assert_writable_target(target, data, manifest, hash_key):
        raise ManagerError(
            "conflict",
            f"Existing agent file differs from the router-managed target: {target}",
            {"path": str(target)},
        )
    if target.is_file() and target.read_text() == text:
        return False
    atomic_write(target, data, mode=0o644)
    return True


def install_catalog(paths: Paths, manifest: Dict[str, Any]) -> bool:
    """Write the dual-model catalog. Foreign existing content is a conflict, never overwritten."""
    data = (json.dumps(catalog_payload(), ensure_ascii=False, indent=2) + "\n").encode()
    if paths.catalog.is_file() and not _assert_writable_target(paths.catalog, data, manifest, "catalog"):
        raise ManagerError(
            "conflict",
            f"Existing model catalog differs from the router-managed target: {paths.catalog}",
            {"path": str(paths.catalog)},
        )
    if paths.catalog.is_file() and paths.catalog.read_text() == data.decode():
        return False
    atomic_write(paths.catalog, data)
    return True


def install_runtime_skill(paths: Paths, manifest: Dict[str, Any]) -> bool:
    source = package_root() / "skills" / "use-deepseek-router" / "SKILL.md"
    if not source.is_file():
        raise ManagerError("skill_source_missing", f"Runtime skill source not found: {source}")
    data = source.read_bytes()
    target = paths.runtime_skill_dir / "SKILL.md"
    if target.is_file() and not _assert_writable_target(target, data, manifest, "runtime_skill"):
        raise ManagerError(
            "conflict",
            f"Existing runtime skill differs from the router-managed target: {target}",
            {"path": str(target)},
        )
    if target.is_file() and target.read_text() == data.decode():
        return False
    atomic_write(target, data, mode=0o644)
    return True


def hook_entry_json(command: str, status_message: str) -> Dict[str, Any]:
    return {
        "type": "command",
        "command": command,
        "timeout": 10,
        "statusMessage": status_message,
        "additionalContextLimit": 0,
    }


def our_hook_config(paths: Paths) -> Dict[str, Any]:
    python = sys.executable or "python3"
    handoff_script = paths.hooks_install_dir / "plaintext_handoff.py"
    command = (
        f'{python} "{handoff_script}" --mode hook --state-directory "{paths.handoff_dir}"'
    )
    entry = hook_entry_json(command, "Delivering DeepSeek child assignment")
    if platform_name() == "windows":
        ps1_script = paths.hooks_install_dir / "plaintext-handoff.ps1"
        windows_command = (
            'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass '
            f'-File "{ps1_script}" -Mode hook -StateDirectory "{paths.handoff_dir}"'
        )
        entry["commandWindows"] = windows_command
    return {
        "description": "Plaintext task handoff for DeepSeek child agents.",
        "hooks": {
            "SubagentStart": [
                {
                    "matcher": "^(deepseek_flash|deepseek_pro)$",
                    "hooks": [entry],
                }
            ]
        },
    }


def _matcher_equal(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    def normalize(entry: Dict[str, Any]) -> Dict[str, Any]:
        copied = dict(entry)
        copied.pop("statusMessage", None)
        copied.pop("description", None)
        return copied

    return normalize(a) == normalize(b)


def _entry_is_ours(entry: Dict[str, Any], paths: Paths) -> bool:
    """Recognize a previously installed router entry even when the command
    path changed (interpreter upgrade, moved skill directory)."""
    inner = (entry.get("hooks") or [{}])[0] if isinstance(entry, dict) else {}
    command = inner.get("command", "") if isinstance(inner, dict) else ""
    command_windows = inner.get("commandWindows", "") if isinstance(inner, dict) else ""

    def basename(value: str) -> str:
        return re.split(r"[\\/]", value.strip('"'))[-1].lower()

    recognized = False
    if command:
        if re.search(r"[;&|`$><\r\n]", command):
            return False
        try:
            tokens = shlex.split(command)
        except ValueError:
            return False
        if not (
            len(tokens) == 6
            and basename(tokens[1]) == "plaintext_handoff.py"
            and tokens[2:5] == ["--mode", "hook", "--state-directory"]
            and tokens[0]
            and tokens[5]
        ):
            return False
        recognized = True
    if command_windows:
        if re.search(r"[;&|`$><\r\n]", command_windows):
            return False
        try:
            tokens = [token.strip('"') for token in shlex.split(command_windows, posix=False)]
        except ValueError:
            return False
        lowered = [token.lower() for token in tokens]
        if not (
            len(tokens) == 11
            and basename(tokens[0]) == "powershell.exe"
            and lowered[1:6] == [
                "-noprofile",
                "-noninteractive",
                "-executionpolicy",
                "bypass",
                "-file",
            ]
            and basename(tokens[6]) == "plaintext-handoff.ps1"
            and lowered[7:10] == ["-mode", "hook", "-statedirectory"]
            and tokens[10]
        ):
            return False
        recognized = True
    return recognized


def merge_hook_config(existing: Dict[str, Any], ours: Dict[str, Any], paths: Paths) -> Tuple[Dict[str, Any], bool]:
    """Merge our SubagentStart entry into an existing hooks.json, preserving unrelated hooks.

    Returns (merged, adopted) where adopted is True when an equivalent entry already existed.
    """
    merged = json.loads(json.dumps(existing))
    existing_hooks = merged.get("hooks", {}) if isinstance(merged, dict) else {}
    if not isinstance(existing_hooks, dict):
        raise ManagerError("conflict", "Existing hooks.json has an invalid 'hooks' section.", {})
    subagent_entries = existing_hooks.get("SubagentStart", [])
    if not isinstance(subagent_entries, list):
        raise ManagerError("conflict", "Existing hooks.json 'SubagentStart' section is not a list.", {})
    our_matcher = ours["hooks"]["SubagentStart"][0]
    for index, existing_entry in enumerate(subagent_entries):
        if not isinstance(existing_entry, dict):
            continue
        if existing_entry.get("matcher") == our_matcher["matcher"]:
            if _matcher_equal(existing_entry, our_matcher):
                return existing, True  # adopt: equivalent entry already installed
            if _entry_is_ours(existing_entry, paths):
                subagent_entries[index] = our_matcher  # refresh our own entry in place
                return merged, True
            raise ManagerError(
                "conflict",
                "An existing SubagentStart hook uses the deepseek_flash/deepseek_pro matcher "
                "but differs from the router-managed entry. It will not be overwritten.",
                {"matcher": our_matcher["matcher"]},
            )
    merged.setdefault("hooks", {})
    merged["hooks"].setdefault("SubagentStart", [])
    merged["hooks"]["SubagentStart"].append(our_matcher)
    return merged, False


def install_hook_files(paths: Paths, manifest: Dict[str, Any]) -> None:
    """Copy the handoff scripts into the codex home. Foreign files are conflicts."""
    source_dir = package_root() / "hooks"
    paths.hooks_install_dir.mkdir(parents=True, exist_ok=True)
    for name, hash_key in (
        ("plaintext_handoff.py", "hook_script_py"),
        ("plaintext-handoff.ps1", "hook_script_ps1"),
    ):
        source = source_dir / name
        target = paths.hooks_install_dir / name
        if not source.is_file():
            continue
        data = source.read_bytes()
        if target.is_file() and not _assert_writable_target(target, data, manifest, hash_key):
            raise ManagerError(
                "conflict",
                f"Existing hook script differs from the router-managed target: {target}",
                {"path": str(target)},
            )
        atomic_write(target, data, mode=0o644)


def install_hook_config(paths: Paths, manifest: Dict[str, Any]) -> Tuple[bool, bool]:
    """Install the handoff scripts and merge the hook entry into hooks.json.

    Returns (changed, adopted_existing).
    """
    if paths.config.is_file():
        if toml_has_table(paths.config.read_text(), "hooks"):
            raise ManagerError(
                "inline_hook_config_unsupported",
                "config.toml already contains inline hook configuration. This router does not "
                "merge into inline hook tables; migrate them to hooks.json first or review /hooks.",
            )
    install_hook_files(paths, manifest)
    ours = our_hook_config(paths)
    existing: Dict[str, Any] = {}
    if paths.hooks_config.is_file():
        try:
            existing = json.loads(paths.hooks_config.read_text())
        except json.JSONDecodeError as exc:
            raise ManagerError("conflict", f"Existing hooks.json is not valid JSON: {exc}") from exc
        if not isinstance(existing, dict):
            raise ManagerError("conflict", "Existing hooks.json is not a JSON object.", {})
        # hooks.json is always shared, even when its last observed bytes match
        # our manifest. Preserve unrelated entries on every setup/repair.
        merged, adopted = merge_hook_config(existing, ours, paths)
        data = (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode()
        changed = sha256_file(paths.hooks_config) != sha256_bytes(data)
        if changed:
            atomic_write(paths.hooks_config, data)
        return changed, adopted
    data = (json.dumps(ours, ensure_ascii=False, indent=2) + "\n").encode()
    changed = not paths.hooks_config.is_file() or paths.hooks_config.read_text() != data.decode()
    if changed:
        atomic_write(paths.hooks_config, data)
    return changed, False


def hook_trusted(paths: Paths) -> bool:
    """Heuristic: Codex records the trusted hook command (or its config) in config.toml.

    We never write or forge that state ourselves; it appears only after the user
    reviews the hook with /hooks. The check is deliberately lenient: any mention
    of our installed handoff script path inside config.toml counts as trusted.
    """
    if not paths.config.is_file():
        return False
    script = paths.hooks_install_dir / "plaintext_handoff.py"
    ps1 = paths.hooks_install_dir / "plaintext-handoff.ps1"
    text = paths.config.read_text()
    return str(script) in text or str(ps1) in text


def hook_entry_present(paths: Paths) -> bool:
    """True when hooks.json still contains a router-equivalent SubagentStart entry."""
    if not paths.hooks_config.is_file():
        return False
    try:
        config = json.loads(paths.hooks_config.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    entries = (config.get("hooks") or {}).get("SubagentStart") if isinstance(config, dict) else None
    if not isinstance(entries, list):
        return False
    ours = our_hook_config(paths)["hooks"]["SubagentStart"][0]
    router_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("matcher") == ours["matcher"]
    ]
    return len(router_entries) == 1 and _matcher_equal(router_entries[0], ours)


def runtime_assets_valid(paths: Paths, manifest: Dict[str, Any]) -> bool:
    """All executable/runtime assets must match the installed manifest exactly."""
    return all(
        _file_is_ours(asset.path, manifest, key)
        for key, asset in managed_assets(paths).items()
        if asset.verify_integrity
    )


def hook_files_installed(paths: Paths, manifest: Dict[str, Any]) -> bool:
    return all(
        _file_is_ours(asset.path, manifest, key)
        for key, asset in managed_assets(paths).items()
        if asset.hook_runtime
    )


def apply_managed_assets(paths: Paths, manifest: Dict[str, Any]) -> Tuple[Dict[str, bool], bool]:
    """Install/refresh every managed asset. Returns (changed, hook_adopted)."""
    changed: Dict[str, bool] = {}
    changed["catalog"] = install_catalog(paths, manifest)
    changed["flash_agent"] = install_agent(paths, AGENT_SPECS[FLASH_ROLE], manifest)
    changed["pro_agent"] = install_agent(paths, AGENT_SPECS[PRO_ROLE], manifest)
    changed["runtime_skill"] = install_runtime_skill(paths, manifest)
    hook_changed, hook_adopted = install_hook_config(paths, manifest)
    changed["hook"] = hook_changed
    return changed, hook_adopted


def compute_asset_hashes(paths: Paths) -> Dict[str, str]:
    return {
        key: sha256_file(path)
        for key, path in managed_asset_paths(paths).items()
    }


# ---------------------------------------------------------------------------
# Static status
# ---------------------------------------------------------------------------


def parent_config_snapshot(paths: Paths) -> Dict[str, Optional[str]]:
    if not paths.config.is_file():
        return {"parent_model": None, "parent_provider": None}
    text = paths.config.read_text()
    return {
        "parent_model": toml_get_top_level_string(text, "model"),
        "parent_provider": toml_get_top_level_string(text, "model_provider"),
    }


def agent_status(paths: Paths, manifest: Dict[str, Any], spec: AgentSpec) -> Dict[str, Any]:
    target = paths.agent_path(spec.role)
    hash_key = "flash_agent" if spec.role == FLASH_ROLE else "pro_agent"
    valid = target.is_file() and target.read_text() == agent_toml_text(spec)
    return {
        "installed": target.is_file(),
        "valid": valid,
        "managed": _file_is_ours(target, manifest, hash_key),
        "model": spec.model,
    }


def catalog_status(paths: Paths) -> Dict[str, Any]:
    registered = False
    if paths.catalog.is_file():
        try:
            data = json.loads(paths.catalog.read_text())
            slugs = {item.get("slug") for item in data.get("models", [])}
            registered = all(model in slugs for model in SUPPORTED_ROLES.values())
        except (OSError, json.JSONDecodeError):
            registered = False
    return {"path": str(paths.catalog), "registered": registered}


def static_status(paths: Paths, codex_bin: Optional[str] = None) -> Dict[str, Any]:
    manifest = read_manifest(paths)
    installed = bool(manifest)
    snapshot = parent_config_snapshot(paths)
    original = manifest.get("original") or {}
    parent_unchanged = (
        installed
        and original.get("parent_model") == snapshot["parent_model"]
        and original.get("parent_provider", None) == snapshot.get("parent_provider", None)
    )

    checks: Dict[str, Any] = {
        "flash_agent": agent_status(paths, manifest, AGENT_SPECS[FLASH_ROLE]),
        "pro_agent": agent_status(paths, manifest, AGENT_SPECS[PRO_ROLE]),
        "catalog": catalog_status(paths),
    }
    entry_present = hook_entry_present(paths)
    scripts_present = hook_files_installed(paths, manifest)
    hooks_installed = entry_present and scripts_present
    assets_valid = runtime_assets_valid(paths, manifest)
    trusted = hook_trusted(paths)
    errors: List[str] = []

    codex_path: Optional[str] = None
    codex_version: Optional[str] = None
    if codex_bin:
        try:
            codex_version = codex_version_text(codex_bin)
            codex_path = codex_bin
        except ManagerError as exc:
            errors.append(str(exc))

    backup_count = 0
    if paths.backups_dir.is_dir():
        backup_count = sum(1 for entry in paths.backups_dir.iterdir() if entry.is_dir())

    last_test = manifest.get("last_test")
    test_evidence = bool(
        last_test and last_test.get("flash") and last_test.get("pro")
    )
    # Live-test evidence stays valid only while the managed files it tested
    # are still byte-identical to the installed versions.
    evidence_fresh = test_evidence and assets_valid and hook_entry_present(paths)

    if installed:
        static_ok = (
            checks["flash_agent"]["valid"]
            and checks["pro_agent"]["valid"]
            and checks["catalog"]["registered"]
            and assets_valid
            and hooks_installed
            and credential_present()
            and parent_unchanged
        )
        if manifest.get("disabled"):
            status = "disabled"
        elif static_ok and evidence_fresh:
            status = "ready"
        elif static_ok:
            status = "configured"
        else:
            status = "partial"
    else:
        status = "not_installed"

    return result(
        status,
        installed=installed,
        runtime={
            "platform": platform_name(),
            "python": sys.version.split()[0],
            "codex_path": codex_path,
            "codex_version": codex_version,
            "codex_detected": codex_path is not None,
        },
        parent={
            "model": snapshot["parent_model"],
            "provider": snapshot["parent_provider"],
            "unchanged": parent_unchanged,
        },
        agents={
            FLASH_ROLE: checks["flash_agent"],
            PRO_ROLE: checks["pro_agent"],
        },
        provider={
            "registered": checks["flash_agent"]["valid"] and checks["pro_agent"]["valid"],
            "top_level_untouched": parent_unchanged,
        },
        credential={"backend": credential_backend(), "present": credential_present()},
        hook={
            "installed": hooks_installed,
            "entry_present": entry_present,
            "files_installed": scripts_present,
            "trusted": trusted,
            "review_required": installed and hooks_installed and not trusted,
        },
        hook_trusted=trusted,
        transport_mode=choose_transport(False, hooks_installed).value,
        catalog=checks["catalog"],
        backup={"count": backup_count},
        last_test=last_test,
        errors=errors,
    )


def validate_static_configuration(paths: Paths) -> None:
    status = static_status(paths)
    if status["status"] not in {"configured", "disabled", "ready"}:
        problems = [
            name for name, value in status["agents"].items() if not value["valid"]
        ]
        if not status["catalog"]["registered"]:
            problems.append("catalog")
        if not status["hook"]["installed"]:
            problems.append("hook")
        raise ManagerError(
            "static_validation_failed",
            f"Static validation failed: {', '.join(problems)}",
            status,
        )


# ---------------------------------------------------------------------------
# Lifecycle commands
# ---------------------------------------------------------------------------


def setup(
    paths: Paths,
    codex_bin: str,
    api_key_stdin: bool,
    skip_live_test: bool,
) -> Dict[str, Any]:
    if not credential_present():
        if not api_key_stdin:
            return result(
                "credential_missing",
                message="No DeepSeek API key is available. Pipe it through --api-key-stdin.",
                credential="deepseek_api_key",
            )
        secret = sys.stdin.readline().strip()
        if not secret:
            raise ManagerError("credential_missing", "Standard input contained no API key.")
        store_api_key(secret)
        secret = ""

    manifest = read_manifest(paths)
    previous = manifest or {}
    adopted: Dict[str, bool] = {
        "catalog": bool(paths.catalog.is_file()),
        "hook": False,
    }
    hook_config_preexisted = bool(paths.hooks_config.is_file())
    backup = make_backup(paths)
    changed: Dict[str, bool] = {}
    try:
        # 1. Codex environment
        detect_version = codex_version_text(codex_bin)
        # 2/3. Parent isolation snapshot
        snapshot = parent_config_snapshot(paths)
        if not snapshot["parent_model"]:
            raise ManagerError(
                "parent_model_unconfigured",
                "config.toml has no explicit top-level non-DeepSeek parent model.",
            )
        # 4-7. Managed assets: catalog, both agents, runtime skill, handoff hook.
        changed, hook_adopted = apply_managed_assets(paths, previous)
        adopted["hook"] = hook_adopted

        new_manifest = default_manifest(snapshot["parent_model"], snapshot["parent_provider"])
        new_manifest["adopted_existing"].update(
            {
                "provider": False,
                "catalog": adopted["catalog"],
                "hook": adopted["hook"],
            }
        )
        new_manifest["preexisted"] = {
            "catalog": adopted["catalog"],
            "hook_config": hook_config_preexisted,
        }
        new_manifest["hashes"] = compute_asset_hashes(paths)
        write_manifest(paths, new_manifest)
        validate_static_configuration(paths)
    except Exception:
        restore_backup(paths, backup)
        raise

    payload = result(
        "configured",
        changed=changed,
        adopted_existing=adopted,
        codex_version=detect_version,
        backup=str(backup),
        restart_required=True,
        hook_review_required=True,
        new_task_required=True,
        data_boundary=(
            "Task text, related code context and tool results sent to the DeepSeek child "
            "agents are transmitted to the DeepSeek provider. Environment files, tokens, "
            "passwords and private keys are never placed in the handoff by this tool."
        ),
    )
    if platform_name() == "windows":
        payload["desktop_env_required"] = API_KEY_ENV
        payload["message"] = (
            "The Windows agent templates authenticate via the DEEPSEEK_API_KEY environment "
            "variable. Set it as a user environment variable and fully restart the Codex "
            "desktop app so the runtime inherits it."
        )
    elif platform_name() == "linux":
        payload["desktop_env_required"] = API_KEY_ENV
    if not skip_live_test:
        payload["message"] = (
            payload.get("message", "")
            + " Live smoke tests are not run by setup; run `test` afterwards."
        ).strip()
    return payload


def repair(paths: Paths, codex_bin: str) -> Dict[str, Any]:
    manifest = read_manifest(paths)
    if not manifest:
        raise ManagerError("not_managed", "No router manifest found. Run setup first.")
    manifest["disabled"] = False
    backup = make_backup(paths)
    try:
        changed, hook_adopted = apply_managed_assets(paths, manifest)
        manifest["adopted_existing"] = manifest.get("adopted_existing", {})
        if hook_adopted:
            manifest["adopted_existing"]["hook"] = True
        manifest["hashes"] = compute_asset_hashes(paths)
        manifest["hash_version"] = HASH_VERSION_EXACT_BYTES
        # Repair may refresh executable assets or routing configuration. Prior
        # live-smoke evidence cannot certify the repaired installation.
        manifest["last_test"] = None
        # Parent model may have changed (user upgraded); refresh the recorded snapshot.
        snapshot = parent_config_snapshot(paths)
        manifest["original"] = {
            "parent_model": snapshot["parent_model"],
            "parent_provider": snapshot["parent_provider"],
        }
        write_manifest(paths, manifest)
        validate_static_configuration(paths)
    except Exception:
        restore_backup(paths, backup)
        raise
    return result(
        "configured",
        changed=changed,
        backup=str(backup),
        restart_required=True,
        hook_review_required=not hook_trusted(paths),
        new_task_required=True,
    )


def remove_our_hook_entry(paths: Paths, manifest: Dict[str, Any]) -> bool:
    """Remove only the router's SubagentStart entry from hooks.json. Returns True when changed."""
    if not paths.hooks_config.is_file():
        return False
    try:
        config = json.loads(paths.hooks_config.read_text())
    except json.JSONDecodeError as exc:
        raise ManagerError("conflict", f"hooks.json is not valid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise ManagerError("conflict", "hooks.json is not a JSON object.", {})
    entries = (config.get("hooks") or {}).get("SubagentStart")
    if not isinstance(entries, list):
        return False
    matcher = "^(deepseek_flash|deepseek_pro)$"
    ours = our_hook_config(paths)["hooks"]["SubagentStart"][0]
    kept: List[Dict[str, Any]] = []
    changed = False
    for entry in entries:
        if isinstance(entry, dict) and entry.get("matcher") == matcher:
            if not (_matcher_equal(entry, ours) or _entry_is_ours(entry, paths)):
                raise ManagerError(
                    "conflict",
                    "The router hook entry in hooks.json was modified by the user; refusing to touch it.",
                    {"matcher": matcher},
                )
            changed = True
            continue
        kept.append(entry)
    if not changed:
        return False
    config["hooks"]["SubagentStart"] = kept
    if not kept:
        config["hooks"].pop("SubagentStart", None)
    if not config["hooks"]:
        config.pop("hooks", None)
    atomic_write(paths.hooks_config, (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode())
    return True


def disable(paths: Paths) -> Dict[str, Any]:
    manifest = read_manifest(paths)
    if not manifest:
        raise ManagerError("not_managed", "No router manifest found. Run setup first.")
    backup = make_backup(paths)
    try:
        changed = remove_our_hook_entry(paths, manifest)
        manifest["disabled"] = True
        write_manifest(paths, manifest)
    except Exception:
        restore_backup(paths, backup)
        raise
    return result(
        "disabled",
        changed=changed,
        credential_preserved=credential_present(),
        catalog_preserved=paths.catalog.is_file(),
        backup=str(backup),
    )


def uninstall(paths: Paths, remove_credential: bool) -> Dict[str, Any]:
    manifest = read_manifest(paths)
    if not manifest:
        raise ManagerError("not_managed", "No router manifest found. Refusing to modify configuration.")
    asset_specs = managed_assets(paths)
    assets = {key: asset.path for key, asset in asset_specs.items()}
    for key, asset in asset_specs.items():
        if asset.shared:
            continue  # shared file: remove only our exact entry below
        target = asset.path
        if target.is_file() and not _file_is_ours(target, manifest, key):
            raise ManagerError(
                "conflict",
                f"Managed file was modified since installation; refusing to remove it: {target}",
                {"path": str(target)},
            )
    backup = make_backup(paths)
    try:
        remove_our_hook_entry(paths, manifest)
        # If hooks.json was created by us and no hooks remain, remove the file.
        if not manifest.get("preexisted", {}).get("hook_config") and paths.hooks_config.is_file():
            try:
                remaining = json.loads(paths.hooks_config.read_text())
            except (OSError, json.JSONDecodeError):
                remaining = None
            if isinstance(remaining, dict) and not remaining.get("hooks"):
                paths.hooks_config.unlink(missing_ok=True)
        for target in (assets["flash_agent"], assets["pro_agent"]):
            target.unlink(missing_ok=True)
        catalog_removed = False
        catalog_restored = False
        if paths.catalog.is_file():
            if manifest.get("preexisted", {}).get("catalog"):
                original = backup / paths.catalog.name
                if original.is_file():
                    atomic_write(paths.catalog, original.read_bytes())
                    catalog_restored = True
            else:
                paths.catalog.unlink(missing_ok=True)
                catalog_removed = True
        runtime_skill = assets["runtime_skill"]
        runtime_skill.unlink(missing_ok=True)
        shutil.rmtree(paths.runtime_skill_dir, ignore_errors=True)
        shutil.rmtree(paths.hooks_install_dir, ignore_errors=True)
        # Final sweep of the state dir (manifest, backups, handoff state).
        shutil.rmtree(paths.state_dir, ignore_errors=True)
    except Exception:
        restore_backup(paths, backup)
        raise
    removed_credential = remove_api_key() if remove_credential else False
    return result(
        "uninstalled",
        catalog_removed=catalog_removed,
        catalog_restored=catalog_restored,
        credential_removed=removed_credential,
        backup=str(backup),
    )


def doctor(paths: Paths, codex_bin: Optional[str]) -> Dict[str, Any]:
    status = static_status(paths, codex_bin)
    handoff_state: Dict[str, Any] = {"directory": str(paths.handoff_dir), "files": []}
    if paths.handoff_dir.is_dir():
        names = sorted(entry.name for entry in paths.handoff_dir.iterdir())
        handoff_state["files"] = names
        handoff_state["active_pending"] = any(name.endswith(".pending.json") for name in names)
    top_level_deepseek_provider = False
    if paths.config.is_file():
        top_level_deepseek_provider = toml_has_table(paths.config.read_text(), f"model_providers.{PROVIDER}")
    payload = {
        **status,
        "handoff_state": handoff_state,
        "top_level_deepseek_provider": top_level_deepseek_provider,
        "notes": [
            "Top-level config.toml is never modified by this router.",
            "The DeepSeek provider is declared inside each agent TOML.",
        ],
    }
    return payload


# ---------------------------------------------------------------------------
# Live smoke tests (dual oracle: thread metadata + marker)
# ---------------------------------------------------------------------------


def query_child_metadata(
    paths: Paths, child_id: str, deadline: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    candidates: List[Tuple[float, Path]] = []
    for state_db in paths.codex_home.glob("state_*.sqlite"):
        try:
            candidates.append((state_db.stat().st_mtime, state_db))
        except OSError:
            continue
    for _, state_db in sorted(candidates, reverse=True)[:MAX_STATE_DATABASES]:
        if deadline is not None and time.monotonic() >= deadline:
            return None
        try:
            with sqlite3.connect(
                f"{state_db.resolve().as_uri()}?mode=ro", uri=True, timeout=0.05
            ) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(threads)").fetchall()
                }
                required = {"id", "model_provider", "model", "agent_role"}
                if not required.issubset(columns):
                    continue
                row = connection.execute(
                    "SELECT model_provider, model, agent_role FROM threads WHERE id = ?",
                    (child_id,),
                ).fetchone()
        except (OSError, sqlite3.Error):
            continue
        if row:
            return {"model_provider": row[0], "model": row[1], "agent_role": row[2]}
    return None


def wait_for_child_metadata(
    paths: Paths,
    child_id: str,
    timeout_seconds: float = METADATA_WAIT_SECONDS,
    poll_interval: float = 0.2,
) -> Optional[Dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        metadata = query_child_metadata(paths, child_id, deadline)
        if metadata is not None:
            return metadata
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(poll_interval, remaining))


def _smoke_env(paths: Paths) -> Dict[str, str]:
    env = dict(os.environ)
    env["CODEX_HOME"] = str(paths.codex_home)
    # Make the key visible to the spawned child process regardless of the
    # TOML auth style (env_key on Windows/Linux, Keychain command on macOS).
    # The value is never logged or returned.
    try:
        key = read_credential_key()
    except ManagerError:
        key = None
    if key:
        env[API_KEY_ENV] = key
    return env


def _stage_command(paths: Paths, role: str, expected_line: str) -> str:
    """The parent runs this exact command with the Bash tool before spawning."""
    if platform_name() == "windows":
        ps1 = paths.hooks_install_dir / "plaintext-handoff.ps1"
        return (
            f"printf '%s' 'Compute 17*23 and reply exactly this line: {expected_line} 391' | "
            f'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass '
            f'-File "{ps1}" -Mode stage -AgentType {role} -Policy FAST '
            f'-Modality TEXT_ONLY -StateDirectory "{paths.handoff_dir}"'
        )
    handoff_script = paths.hooks_install_dir / "plaintext_handoff.py"
    return (
        f"printf '%s' 'Compute 17*23 and reply exactly this line: {expected_line} 391' | "
        f'python3 "{handoff_script}" --mode stage --agent-type {role} --policy FAST '
        f'--modality TEXT_ONLY --state-directory "{paths.handoff_dir}"'
    )


def native_spawn_smoke(paths: Paths, codex_bin: str, role: str, model: str) -> Dict[str, Any]:
    """Prove: Parent -> spawn_agent -> DeepSeek child -> callback, for one role."""
    parent_model = parent_config_snapshot(paths)["parent_model"]
    if not parent_model:
        raise ManagerError("parent_model_unconfigured", "config.toml has no explicit parent model.")
    marker = uuid.uuid4().hex
    expected_line = f"NATIVE_{role.upper()}_OK {marker}"
    stage_command = _stage_command(paths, role, expected_line)
    prompt = (
        f"Run this exact command with the Bash tool:\n{stage_command}\n"
        f"Then use the spawn_agent tool exactly once with agent_type {role} and fork_turns none.\n"
        "Then use the wait tool to wait for that subagent.\n"
        "Then reply with only the final response of the subagent."
    )
    env = _smoke_env(paths)
    try:
        proc = subprocess.run(
            [
                codex_bin,
                "exec",
                "--skip-git-repo-check",
                "--json",
                "-s",
                "workspace-write",
                "-C",
                str(paths.codex_home),
                "-m",
                parent_model,
                prompt,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        raise ManagerError(
            "child_timeout",
            f"Smoke test for {role} timed out before the child returned.",
        ) from exc
    if proc.returncode != 0:
        stderr = proc.stderr[-1200:]
        if "hook" in stderr.lower() and not hook_trusted(paths):
            raise ManagerError(
                "hook_untrusted",
                "The plaintext handoff hook has not been reviewed. Run /hooks in Codex and trust the hook, then retry.",
                {"stderr": stderr},
            )
        raise ManagerError(
            "child_start_failed",
            f"Native spawn_agent smoke for {role} failed.",
            {"stderr": stderr},
        )

    child_ids: List[str] = []
    child_messages: Dict[str, str] = {}
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") or {}
        if (
            event.get("type") == "item.completed"
            and item.get("type") == "collab_tool_call"
            and item.get("tool") == "spawn_agent"
        ):
            child_ids.extend(item.get("receiver_thread_ids") or [])
        if (
            event.get("type") == "item.completed"
            and item.get("type") == "collab_tool_call"
            and item.get("tool") == "wait"
        ):
            for receiver_id, state in (item.get("agents_states") or {}).items():
                if not isinstance(state, dict):
                    continue
                message = state.get("message")
                if state.get("status") == "completed" and isinstance(message, str):
                    child_messages[receiver_id] = message.strip()

    child_id = child_ids[0] if len(child_ids) == 1 else None
    child_message = child_messages.get(child_id) if child_id else None
    metadata = wait_for_child_metadata(paths, child_id) if child_id else None
    expected = {"model_provider": PROVIDER, "model": model, "agent_role": role}
    marker_ok = bool(child_message) and expected_line in child_message and "391" in child_message
    if len(child_ids) != 1 or not marker_ok or metadata != expected:
        raise ManagerError(
            "native_route_mismatch",
            f"Native child routing evidence for {role} is incomplete or does not match the expected DeepSeek configuration.",
            {
                "child_ids": child_ids,
                "child_message": child_message,
                "metadata": metadata,
                "expected": expected,
                "expected_line": expected_line,
            },
        )
    return {
        "role": role,
        "model": model,
        "model_provider": PROVIDER,
        "agent_role": role,
        "marker_verified": True,
        "child_id": child_id,
    }


def run_tests(paths: Paths, codex_bin: str) -> Dict[str, Any]:
    status = static_status(paths, codex_bin)
    if status["status"] not in {"configured", "ready"}:
        raise ManagerError("not_configured", "Static configuration is incomplete; live tests cannot run.", status)
    if not hook_trusted(paths):
        raise ManagerError(
            "hook_untrusted",
            "The plaintext handoff hook has not been reviewed yet. Open /hooks in Codex, trust the hook, then run test again.",
        )
    results: Dict[str, Any] = {}
    for role, model in SUPPORTED_ROLES.items():
        results[role] = native_spawn_smoke(paths, codex_bin, role, model)
    manifest = read_manifest(paths)
    manifest["last_test"] = {
        "ran_at": datetime.now().isoformat(timespec="seconds"),
        "flash": results[FLASH_ROLE],
        "pro": results[PRO_ROLE],
    }
    write_manifest(paths, manifest)
    return result(
        "ready",
        flash=results[FLASH_ROLE],
        pro=results[PRO_ROLE],
        new_task_required=True,
        restart_required=True,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("status", "setup", "test", "repair", "disable", "uninstall", "doctor"),
    )
    parser.add_argument("--codex-home")
    parser.add_argument("--api-key-stdin", action="store_true")
    parser.add_argument("--skip-live-test", action="store_true")
    parser.add_argument("--remove-credential", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = resolve_paths(args.codex_home)
    try:
        codex_bin: Optional[str] = None
        if args.command in {"status", "setup", "repair", "test", "doctor"}:
            try:
                codex_bin = find_desktop_codex()
            except ManagerError as exc:
                if args.command in {"setup", "repair", "test"}:
                    raise
                codex_bin = None  # status/doctor degrade gracefully
        if args.command == "status":
            payload = static_status(paths, codex_bin)
        elif args.command == "doctor":
            payload = doctor(paths, codex_bin)
        else:
            with operation_lock(paths):
                if args.command == "setup":
                    payload = setup(paths, codex_bin or "", args.api_key_stdin, args.skip_live_test)
                elif args.command == "repair":
                    payload = repair(paths, codex_bin or "")
                elif args.command == "test":
                    payload = run_tests(paths, codex_bin or "")
                elif args.command == "disable":
                    payload = disable(paths)
                else:
                    payload = uninstall(paths, args.remove_credential)
        emit(payload, args.json)
        return 0 if payload["status"] not in {"partial", "credential_missing"} else 2
    except ManagerError as exc:
        emit(result(exc.code, message=str(exc), **exc.details), args.json)
        return 2
    except subprocess.TimeoutExpired:
        emit(result("timeout", message="Operation timed out. No credential was printed."), args.json)
        return 3
    except Exception as exc:  # noqa: BLE001 - top-level safety net, never prints secrets
        emit(result("failed", message=f"{type(exc).__name__}: {exc}"), args.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
