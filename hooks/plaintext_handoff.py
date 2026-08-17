#!/usr/bin/env python3
"""One-shot plaintext task handoff for the DeepSeek child agents.

Adapted from Utopia-V/codex-deepseek-subagent (MIT), hooks/plaintext_handoff.py.
See docs/upstream-reference-map.md for the per-symbol source map.

Protocol (per agent role):

    stage        parent writes deepseek_flash.pending.json /
                 deepseek_pro.pending.json under a per-role OS lock
    SubagentStart hook claims it atomically (pending -> claimed.<agent_id>.<uuid>)
    validate     schema / UUID / timestamps / agent type / size / encoding
    consume      one-shot delivery via hookSpecificOutput.additionalContext
    quarantine   malformed claims move to <role>.failed.*.json

At most one pending/claimed assignment per role at a time, so a task staged
for deepseek_flash can never be delivered to deepseek_pro.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import pathlib
import re
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple

if os.name == "posix":
    import fcntl
else:
    fcntl = None

try:
    import msvcrt
except ImportError:  # macOS / Linux
    msvcrt = None


VALID_AGENTS = {"deepseek_flash", "deepseek_pro"}
POLICIES = {"FAST", "REACT", "SPEC", "DEEP"}
MODALITIES = {"TEXT_ONLY", "VISION_TRANSLATABLE", "VISION_CRITICAL"}
DEFAULT_TTL_SECONDS = 300
MAX_ASSIGNMENT_CHARS = 1_000_000
MAX_PACKET_CHARS = 200_000


class EnvelopeError(ValueError):
    pass


class HandoffBusy(RuntimeError):
    pass


class HandoffLocked(HandoffBusy):
    """State transition already in progress (exit code 13, like the PS1)."""


class HandoffCorrupt(RuntimeError):
    """Existing pending state is malformed; refuse to replace it (exit code 9)."""


class HandoffMissing(RuntimeError):
    pass


class HandoffMalformed(HandoffMissing):
    """Claimed state was malformed and quarantined (exit code 5)."""


class HandoffExpired(HandoffMissing):
    """Claimed state expired before the child started (exit code 6)."""


def state_root(override: Optional[str]) -> pathlib.Path:
    if override:
        return pathlib.Path(override).expanduser().resolve()
    environment_override = os.environ.get("CODEX_DEEPSEEK_ROUTER_HANDOFF_DIR")
    if environment_override:
        return pathlib.Path(environment_override).expanduser().resolve()
    codex_home = os.environ.get("CODEX_HOME") or str(pathlib.Path.home() / ".codex")
    return pathlib.Path(codex_home) / "deepseek-router" / "handoff"


def fail(message: str, code: int) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def fail_open(message: str) -> None:
    """A transport failure must not abort the Codex parent task."""
    print(f"Plaintext handoff skipped: {message}", file=sys.stderr)
    json.dump(
        {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": ""}},
        sys.stdout,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    sys.stdout.flush()


def transport_failure(action: str, error: OSError) -> None:
    fail(f"Plaintext handoff transport failure while {action}: {error}", 12)


# ---------------------------------------------------------------------------
# Locking (per-role)
# ---------------------------------------------------------------------------


def lock_path(root: pathlib.Path, agent_type: str) -> pathlib.Path:
    return root / f".{agent_type}.lock"


def _try_lock_file(lock_file) -> bool:
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
    raise EnvelopeError("No file locking implementation is available on this platform.")


def _unlock_file(lock_file) -> None:
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is not None:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def state_lock(root: pathlib.Path, agent_type: str):
    """Exclusive non-blocking per-role dispatch lock."""
    lock_file = None
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
        descriptor = os.open(lock_path(root, agent_type), os.O_RDWR | os.O_CREAT, 0o600)
        if hasattr(os, "fchmod"):  # not available on Windows
            os.fchmod(descriptor, 0o600)
        lock_file = os.fdopen(descriptor, "a+")
        if not _try_lock_file(lock_file):
            raise HandoffLocked(
                f"A plaintext handoff state transition for {agent_type} is already in progress."
            )
    except HandoffBusy:
        if lock_file is not None:
            lock_file.close()
        raise
    except OSError as error:
        if lock_file is not None:
            lock_file.close()
        transport_failure("acquiring the state lock", error)
    try:
        yield
    finally:
        _unlock_file(lock_file)
        lock_file.close()


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


def parse_timestamp(value: Any, field_name: str) -> datetime.datetime:
    if not isinstance(value, str):
        raise EnvelopeError(f"{field_name} must be a timestamp string")
    try:
        timestamp = datetime.datetime.fromisoformat(value)
    except ValueError as error:
        raise EnvelopeError(f"{field_name} is not a valid timestamp") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise EnvelopeError(f"{field_name} must include a UTC offset")
    return timestamp


def _validate_optional_packet(value: Any, field_name: str, max_chars: int) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise EnvelopeError(f"{field_name} must be a JSON object or null")
    if len(json.dumps(value, ensure_ascii=False)) > max_chars:
        raise EnvelopeError(f"{field_name} exceeds the maximum payload size")


def validate_envelope(value: Any) -> Tuple[Dict[str, Any], datetime.datetime]:
    if not isinstance(value, dict):
        raise EnvelopeError("the handoff envelope must be a JSON object")
    if type(value.get("schema")) is not int or value["schema"] != 1:
        raise EnvelopeError("the handoff envelope has an invalid schema")
    if value.get("agent_type") not in VALID_AGENTS:
        raise EnvelopeError("the handoff envelope has an invalid agent type")
    if not isinstance(value.get("handoff_id"), str) or not value["handoff_id"]:
        raise EnvelopeError("the handoff envelope has an invalid handoff id")
    try:
        uuid.UUID(value["handoff_id"])
    except ValueError as error:
        raise EnvelopeError("the handoff envelope has an invalid handoff id") from error
    if not isinstance(value.get("assignment"), str):
        raise EnvelopeError("the handoff envelope assignment must be a string")
    if not value["assignment"].strip():
        raise EnvelopeError("the handoff envelope assignment must not be blank")
    if len(value["assignment"]) > MAX_ASSIGNMENT_CHARS:
        raise EnvelopeError("the handoff envelope assignment exceeds the maximum payload size")
    if value.get("policy") not in POLICIES:
        raise EnvelopeError("the handoff envelope has an invalid reasoning policy")
    if value.get("modality") not in MODALITIES:
        raise EnvelopeError("the handoff envelope has an invalid modality")
    _validate_optional_packet(value.get("visual_context"), "visual_context", MAX_PACKET_CHARS)
    _validate_optional_packet(value.get("evidence_packet"), "evidence_packet", MAX_PACKET_CHARS)
    created_at = parse_timestamp(value.get("created_at"), "created_at")
    expires_at = parse_timestamp(value.get("expires_at"), "expires_at")
    if expires_at <= created_at:
        raise EnvelopeError("expires_at must be later than created_at")
    return value, expires_at


def new_envelope(
    *,
    agent_type: str,
    assignment: str,
    policy: str,
    modality: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    visual_context: Optional[Dict[str, Any]] = None,
    evidence_packet: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if agent_type not in VALID_AGENTS:
        raise EnvelopeError("invalid agent_type")
    if not assignment.strip():
        raise EnvelopeError("empty assignment")
    if policy not in POLICIES:
        raise EnvelopeError("invalid policy")
    if modality not in MODALITIES:
        raise EnvelopeError("invalid modality")
    if not 1 <= ttl_seconds <= 3600:
        raise EnvelopeError("ttl_seconds must be between 1 and 3600")
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "schema": 1,
        "handoff_id": str(uuid.uuid4()),
        "agent_type": agent_type,
        "created_at": now.isoformat(),
        "expires_at": (now + datetime.timedelta(seconds=ttl_seconds)).isoformat(),
        "assignment": assignment,
        "policy": policy,
        "modality": modality,
        "visual_context": visual_context,
        "evidence_packet": evidence_packet,
    }


# ---------------------------------------------------------------------------
# State files
# ---------------------------------------------------------------------------


def pending_path(root: pathlib.Path, agent_type: str) -> pathlib.Path:
    return root / f"{agent_type}.pending.json"


def quarantine_path(root: pathlib.Path, agent_type: str, agent_id: str) -> pathlib.Path:
    safe_agent_id = re.sub(r"[^A-Za-z0-9_-]", "_", agent_id) or "unknown"
    return root / f"{agent_type}.failed.{safe_agent_id}.{uuid.uuid4().hex}.json"


def claimed_files(root: pathlib.Path, agent_type: str) -> List[pathlib.Path]:
    return sorted(root.glob(f"{agent_type}.claimed.*.json"))


def failed_files(root: pathlib.Path, agent_type: str) -> List[pathlib.Path]:
    return sorted(root.glob(f"{agent_type}.failed.*.json"))


def quarantine_claim(claimed: pathlib.Path, agent_type: str, agent_id: str) -> None:
    failed = quarantine_path(claimed.parent, agent_type, agent_id)
    try:
        claimed.rename(failed)
    except FileNotFoundError:
        pass
    except OSError as error:
        transport_failure("quarantining an invalid claim", error)


def read_envelope_file(path: pathlib.Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def reconcile(root: pathlib.Path, agent_type: str, now: datetime.datetime) -> None:
    """Quarantine malformed claims; delete expired claims (and stale failed files)."""
    if not root.exists():
        return
    for claimed in claimed_files(root, agent_type):
        try:
            _, expires_at = validate_envelope(read_envelope_file(claimed))
        except (EnvelopeError, FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            prefix = f"{agent_type}.claimed."
            agent_id = claimed.name[len(prefix): -len(".json")]
            quarantine_claim(claimed, agent_type, agent_id)
            continue
        except OSError as error:
            transport_failure("checking claimed handoffs", error)
        if expires_at > now:
            continue
        try:
            claimed.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            transport_failure("cleaning an expired claim", error)
    for failed in failed_files(root, agent_type):
        try:
            _, expires_at = validate_envelope(read_envelope_file(failed))
            if expires_at <= now:
                failed.unlink()
        except (EnvelopeError, FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            continue  # malformed quarantine entries stay until resolved manually
        except OSError as error:
            transport_failure("cleaning an expired quarantine entry", error)


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


def stage_locked(
    root: pathlib.Path,
    *,
    agent_type: str,
    assignment: str,
    policy: str,
    modality: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    visual_context: Optional[Dict[str, Any]] = None,
    evidence_packet: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], pathlib.Path]:
    pending = pending_path(root, agent_type)
    now = datetime.datetime.now(datetime.timezone.utc)
    replace_expired = False
    reconcile(root, agent_type, now)
    if claimed_files(root, agent_type) or failed_files(root, agent_type):
        raise HandoffBusy(
            f"A {agent_type} handoff is already claimed or quarantined. "
            "Resolve it before staging another."
        )
    if pending.exists():
        try:
            existing = read_envelope_file(pending)
        except FileNotFoundError:
            existing = None
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            raise HandoffCorrupt(
                f"The existing {agent_type} handoff is malformed. Refusing to replace it."
            )
        if existing is not None:
            try:
                _, expires_at = validate_envelope(existing)
            except EnvelopeError:
                raise HandoffCorrupt(
                    f"The existing {agent_type} handoff has an invalid schema, agent type, "
                    "assignment, or expiry. Refusing to replace it."
                )
            if expires_at > now:
                raise HandoffBusy(
                    f"A {agent_type} handoff is already pending. Let it be consumed or "
                    "expire before staging another."
                )
            replace_expired = True

    envelope = new_envelope(
        agent_type=agent_type,
        assignment=assignment,
        policy=policy,
        modality=modality,
        ttl_seconds=ttl_seconds,
        visual_context=visual_context,
        evidence_packet=evidence_packet,
    )

    temporary = root / f".{agent_type}.staging.{uuid.uuid4().hex}.tmp"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            json.dump(envelope, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        if replace_expired:
            try:
                os.replace(temporary, pending)
            except OSError as error:
                transport_failure("replacing an expired pending handoff", error)
        else:
            try:
                os.link(temporary, pending)
            except FileExistsError:
                raise HandoffBusy(
                    f"A {agent_type} handoff is already pending. Consume or remove it "
                    "before staging another."
                )
            except OSError as error:
                transport_failure("publishing a pending handoff", error)
    except OSError as error:
        transport_failure("writing a pending handoff", error)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            transport_failure("cleaning a staged handoff temporary file", error)
    return envelope, pending


def stage(
    root: pathlib.Path,
    *,
    agent_type: str,
    assignment: str,
    policy: str,
    modality: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    visual_context: Optional[Dict[str, Any]] = None,
    evidence_packet: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    with state_lock(root, agent_type):
        envelope, pending = stage_locked(
            root,
            agent_type=agent_type,
            assignment=assignment,
            policy=policy,
            modality=modality,
            ttl_seconds=ttl_seconds,
            visual_context=visual_context,
            evidence_packet=evidence_packet,
        )
    return {
        "staged": True,
        "handoff_id": envelope["handoff_id"],
        "agent_type": agent_type,
        "expires_at": envelope["expires_at"],
        "pending_path": str(pending),
    }


# ---------------------------------------------------------------------------
# Claim (SubagentStart hook side)
# ---------------------------------------------------------------------------


def claim_pending(
    root: pathlib.Path,
    agent_type: str,
    agent_id: str,
) -> Tuple[Dict[str, Any], pathlib.Path]:
    """Atomically claim the pending assignment for this role.

    Claim first, then validate and read - never read the pending file before
    the atomic rename, so a racing spawn can never receive another role's task.
    Returns (envelope, claimed_path); the caller consumes the claim after
    delivering the child context.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    reconcile(root, agent_type, now)
    pending = pending_path(root, agent_type)
    if claimed_files(root, agent_type) or failed_files(root, agent_type):
        raise HandoffBusy(
            f"A plaintext handoff is already claimed or quarantined for {agent_type}."
        )
    if not pending.exists():
        raise HandoffMissing(f"No plaintext handoff was available for the {agent_type} start.")
    safe_agent_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(agent_id or uuid.uuid4().hex)) or "unknown"
    claimed = root / f"{agent_type}.claimed.{safe_agent_id}.{uuid.uuid4().hex}.json"
    try:
        pending.rename(claimed)
    except FileNotFoundError:
        raise HandoffMissing(f"The plaintext handoff disappeared before it could be claimed.")
    except OSError as error:
        transport_failure("claiming the pending handoff", error)
    try:
        claimed.chmod(0o600)
    except OSError as error:
        transport_failure("securing the claimed handoff", error)

    try:
        envelope = read_envelope_file(claimed)
        envelope, expires_at = validate_envelope(envelope)
    except (EnvelopeError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        quarantine_claim(claimed, agent_type, safe_agent_id)
        raise HandoffMalformed(
            f"The pending {agent_type} handoff is malformed or has an invalid schema."
        )

    if expires_at <= now:
        try:
            claimed.unlink()
        except OSError as error:
            transport_failure("removing an expired pending handoff", error)
        raise HandoffExpired(f"The pending {agent_type} handoff expired before the child started.")

    if envelope["agent_type"] != agent_type:
        quarantine_claim(claimed, agent_type, safe_agent_id)
        raise HandoffMalformed(f"The pending handoff does not match the {agent_type} child.")
    return envelope, claimed


def consume_claim(claimed: pathlib.Path) -> None:
    try:
        claimed.unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        transport_failure("consuming the claimed handoff", error)


# ---------------------------------------------------------------------------
# Child context
# ---------------------------------------------------------------------------


def build_child_context(envelope: Dict[str, Any]) -> str:
    sections = [
        "You are the spawned child agent, not the root agent. The parent supplied the complete "
        "task below through a one-time plaintext handoff because provider-internal "
        "collaboration ciphertext is not a reliable cross-provider task carrier. Treat this as "
        "the task contract. Do not continue the parent's unrelated work and do not report the "
        "assignment missing merely because the encrypted collaboration payload is unreadable.",
        "",
        "BEGIN PARENT ASSIGNMENT",
        envelope["assignment"],
        "END PARENT ASSIGNMENT",
        "",
        f"REASONING_POLICY: {envelope['policy']}",
        f"MODALITY: {envelope['modality']}",
    ]

    visual = envelope.get("visual_context")
    if visual:
        sections.extend(
            [
                "",
                "BEGIN VISUAL CONTEXT",
                json.dumps(visual, ensure_ascii=False, indent=2),
                "END VISUAL CONTEXT",
            ]
        )

    evidence = envelope.get("evidence_packet")
    if evidence:
        sections.extend(
            [
                "",
                "BEGIN EVIDENCE PACKET",
                json.dumps(evidence, ensure_ascii=False, indent=2),
                "END EVIDENCE PACKET",
            ]
        )
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run_stage_cli(root: pathlib.Path, args: argparse.Namespace) -> None:
    if args.json_envelope:
        try:
            envelope = json.load(sys.stdin)
        except json.JSONDecodeError as error:
            fail(f"Envelope input was invalid JSON: {error}", 2)
        if not isinstance(envelope, dict):
            fail("Envelope input must be a JSON object.", 2)
        assignment = envelope.get("assignment", "")
        agent_type = envelope.get("agent_type")
        policy = envelope.get("policy", "FAST")
        modality = envelope.get("modality", "TEXT_ONLY")
        visual_context = envelope.get("visual_context")
        evidence_packet = envelope.get("evidence_packet")
    else:
        assignment = sys.stdin.read()
        agent_type = args.agent_type
        policy = args.policy
        modality = args.modality
        visual_context = None
        evidence_packet = None
    if not assignment.strip():
        fail("Refusing to stage an empty assignment.", 2)
    try:
        payload = stage(
            root,
            agent_type=agent_type,
            assignment=assignment,
            policy=policy,
            modality=modality,
            ttl_seconds=args.ttl_seconds,
            visual_context=visual_context,
            evidence_packet=evidence_packet,
        )
    except HandoffLocked as error:
        fail(str(error), 13)
    except HandoffCorrupt as error:
        fail(str(error), 9)
    except HandoffBusy as error:
        fail(str(error), 3)
    except EnvelopeError as error:
        fail(str(error), 2)
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.flush()


def run_target_hook_locked(root: pathlib.Path, hook_input: Dict[str, Any]) -> None:
    agent_type = hook_input.get("agent_type")
    agent_id = str(hook_input.get("agent_id") or uuid.uuid4().hex)
    try:
        envelope, claimed = claim_pending(root, agent_type, agent_id)
    except HandoffBusy as error:
        raise HandoffBusy(str(error)) from error
    except HandoffMalformed as error:
        raise HandoffMalformed(str(error)) from error
    except HandoffExpired as error:
        raise HandoffExpired(str(error)) from error
    except HandoffMissing as error:
        raise HandoffMissing(str(error)) from error
    context = build_child_context(envelope)
    try:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SubagentStart",
                    "additionalContext": context,
                }
            },
            sys.stdout,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        sys.stdout.flush()
    except OSError as error:
        raise error
    consume_claim(claimed)


def run_hook(root: pathlib.Path) -> None:
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        fail_open(f"SubagentStart hook input was invalid JSON: {error}")
        return
    if not isinstance(hook_input, dict):
        fail_open("SubagentStart hook input must be a JSON object.")
        return
    if (
        hook_input.get("hook_event_name") != "SubagentStart"
        or hook_input.get("agent_type") not in VALID_AGENTS
    ):
        return
    try:
        with state_lock(root, hook_input["agent_type"]):
            run_target_hook_locked(root, hook_input)
    except (HandoffBusy, HandoffMalformed, HandoffExpired, HandoffMissing, HandoffLocked, OSError, ValueError) as error:
        fail_open(str(error))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("stage", "hook"))
    parser.add_argument("--agent-type", choices=sorted(VALID_AGENTS))
    parser.add_argument("--policy", choices=sorted(POLICIES), default="FAST")
    parser.add_argument("--modality", choices=sorted(MODALITIES), default="TEXT_ONLY")
    parser.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    parser.add_argument("--state-directory")
    parser.add_argument(
        "--json-envelope",
        action="store_true",
        help="read a complete assignment envelope as JSON from stdin instead of a plain assignment",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.ttl_seconds <= 3600:
        fail("--ttl-seconds must be between 1 and 3600.", 8)
    root = state_root(args.state_directory)
    if args.mode == "stage":
        if not args.json_envelope and args.agent_type is None:
            fail("--agent-type is required in stage mode.", 8)
        run_stage_cli(root, args)
        return
    run_hook(root)


if __name__ == "__main__":
    main()
