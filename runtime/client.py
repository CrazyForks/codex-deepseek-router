"""Small Responses-compatible DeepSeek client for explicit no-Hook fallback."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from .context import TaskContext, sanitize_context
from .protocol import ErrorCode, RouterError, RoutingResult
from .reasoning import RouteContractError, build_reasoning_context, validate_route_contract


MODELS = {"flash": "deepseek-v4-flash", "pro": "deepseek-v4-pro"}
BASE_URL = "https://api.deepseek.com"
OUTPUT_CONTRACT = (
    "Return JSON only with summary, findings, reasoning_summary, risks, recommendations, confidence, "
    "and evidence fields observed/inferred/recommended/uncertain. Provide only a concise reasoning "
    "summary; do not expose hidden chain-of-thought."
)
FLASH_SPEC_OUTPUT_EXTENSION = (
    " This text-only Flash SPEC fallback always hands its bounded analysis to Pro: set "
    "escalate_to_pro to true and return evidence_packet with "
    "schema, summary, relevant_files, observations, hypotheses, eliminated, open_questions, and "
    "recommended_next_step. Do not claim final resolution, edits, or verification in Flash."
)
GUIDANCE_VARIANTS = {"current", "contract_only", "contract_tuning"}


def _agent_type(mode: str) -> str:
    return "deepseek_flash" if mode == "flash" else "deepseek_pro"


def _default_policy(mode: str) -> str:
    return "FAST" if mode == "flash" else "REACT"


def build_fallback_prompt(
    mode: str,
    policy: str,
    task_context: str,
    *,
    guidance_variant: str = "contract_tuning",
) -> str:
    """Render fallback guidance; non-default variants support reproducible ablation."""
    agent_type = _agent_type(mode)
    try:
        validate_route_contract(agent_type, policy)
    except RouteContractError as error:
        raise RouterError(ErrorCode.CONFIGURATION, str(error)) from error
    if guidance_variant not in GUIDANCE_VARIANTS:
        raise RouterError(
            ErrorCode.CONFIGURATION, f"Unknown reasoning guidance variant: {guidance_variant}"
        )
    if guidance_variant == "current":
        return OUTPUT_CONTRACT + "\n\n" + task_context
    reasoning = build_reasoning_context(agent_type, policy)
    sections = [
        "TASK CONTEXT\n" + task_context,
        "POLICY\n" + policy,
        "POLICY EXECUTION CONTRACT\n" + reasoning.policy_contract,
    ]
    if guidance_variant == "contract_tuning" and reasoning.model_tuning:
        sections.append("MODEL-SPECIFIC TUNING\n" + reasoning.model_tuning)
    output_contract = OUTPUT_CONTRACT
    if agent_type == "deepseek_flash" and policy == "SPEC":
        output_contract += FLASH_SPEC_OUTPUT_EXTENSION
    sections.extend(
        [
            "CONVERGENCE / STOP CONDITION\n" + reasoning.stop_condition,
            "CAPABILITY BOUNDARY\nThis is an explicit text-only fallback request, not the native Codex "
            "subagent tool environment. Use only supplied context; do not claim unprovided tool access, "
            "workspace edits, commands, tests, screenshots, or visual quality. If the assignment has "
            "parent-owned visual criteria, report them as VISUAL_QUALITY_UNVERIFIED for the parent "
            "instead of claiming acceptance.",
            "OUTPUT FORMAT\n" + output_contract,
        ]
    )
    return "\n\n".join(sections)


def _credential_from_manager() -> Optional[str]:
    manager = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "codex_deepseek_router.py")
    try:
        completed = subprocess.run([sys.executable, manager, "_credential-get"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def get_api_key() -> Optional[str]:
    return os.environ.get("DEEPSEEK_API_KEY") or _credential_from_manager()


def _extract_text(payload: Mapping[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    output = payload.get("output")
    if isinstance(output, list):
        chunks = []
        for item in output:
            if not isinstance(item, Mapping):
                continue
            for content in item.get("content") or []:
                if isinstance(content, Mapping) and isinstance(content.get("text"), str):
                    chunks.append(content["text"])
        if chunks:
            return "\n".join(chunks)
    raise RouterError(ErrorCode.INVALID_RESPONSE, "DeepSeek returned no text output")


def _structured(text: str) -> Dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`").split("\n", 1)[-1]
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        value = None
        for index, character in enumerate(candidate):
            if character != "{":
                continue
            try:
                decoded, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                value = decoded
                break
        if value is None:
            return {"summary": candidate, "reasoning_summary": "", "findings": []}
    return value if isinstance(value, dict) else {"summary": candidate}


def _list_field(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, Mapping):
        return [{str(key): item} for key, item in value.items()]
    return [value]


def _string_list(value: Any) -> list:
    rendered = []
    for item in _list_field(value):
        rendered.append(
            item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, sort_keys=True)
        )
    return rendered


def _fallback_evidence_packet(structured: Mapping[str, Any], evidence: Mapping[str, Any]) -> Dict[str, Any]:
    packet = structured.get("evidence_packet")
    packet = dict(packet) if isinstance(packet, Mapping) else {}
    recommendations = _string_list(structured.get("recommendations"))
    recommended = _string_list(evidence.get("recommended"))
    return {
        "schema": 1,
        "summary": str(packet.get("summary") or structured.get("summary") or "Flash SPEC analysis requires Pro continuation."),
        "relevant_files": _string_list(packet.get("relevant_files") or structured.get("relevant_files")),
        "observations": _string_list(packet.get("observations") or evidence.get("observed") or structured.get("findings")),
        "hypotheses": _string_list(packet.get("hypotheses") or evidence.get("inferred")),
        "eliminated": _string_list(packet.get("eliminated")),
        "open_questions": _string_list(packet.get("open_questions") or evidence.get("uncertain")),
        "recommended_next_step": str(
            packet.get("recommended_next_step")
            or (recommendations[0] if recommendations else None)
            or (recommended[0] if recommended else None)
            or "Continue with deepseek_pro using this Evidence Packet."
        ),
    }


@dataclass
class DeepSeekClient:
    mode: str
    base_url: str = BASE_URL
    timeout: float = 45.0
    retries: int = 2
    opener: Callable[..., Any] = urllib.request.urlopen

    def __post_init__(self) -> None:
        if self.mode not in MODELS:
            raise RouterError(ErrorCode.CONFIGURATION, f"Unknown DeepSeek mode: {self.mode}")

    def complete(
        self,
        task: str,
        *,
        context: Optional[Mapping[str, Any]] = None,
        policy: Optional[str] = None,
        cancel_event: Any = None,
        _reasoning_variant: str = "contract_tuning",
    ) -> Dict[str, Any]:
        key = get_api_key()
        if not key:
            raise RouterError(ErrorCode.AUTH, "DeepSeek API key is not configured")
        resolved_policy = _default_policy(self.mode) if policy is None else policy.upper()
        rendered = TaskContext(task=task, relevant_files={"context": sanitize_context(context or {})}).render(self.mode)
        prompt = build_fallback_prompt(
            self.mode,
            resolved_policy,
            rendered,
            guidance_variant=_reasoning_variant,
        )
        body = json.dumps({"model": MODELS[self.mode], "input": prompt}).encode("utf-8")
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        last_error: Optional[RouterError] = None
        for attempt in range(self.retries + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise RouterError(ErrorCode.CANCELLED, "DeepSeek request cancelled")
            request = urllib.request.Request(
                self.base_url.rstrip("/") + "/responses",
                data=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Request-ID": request_id},
                method="POST",
            )
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                text = _extract_text(payload)
                structured = _structured(text)
                evidence = structured.get("evidence")
                evidence = evidence if isinstance(evidence, Mapping) else {}
                if self.mode == "flash" and resolved_policy == "SPEC":
                    evidence_packet = _fallback_evidence_packet(structured, evidence)
                    escalate_to_pro = True
                else:
                    evidence_packet = structured.get("evidence_packet")
                    evidence_packet = (
                        dict(evidence_packet) if isinstance(evidence_packet, Mapping) else {}
                    )
                    escalate_to_pro = structured.get("escalate_to_pro") is True
                usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
                usage = {**usage, "model": MODELS[self.mode], "latency_ms": round((time.monotonic() - started) * 1000), "request_id": request_id}
                return RoutingResult(
                    mode=self.mode,
                    model=MODELS[self.mode],
                    status="completed",
                    summary=str(structured.get("summary", "")),
                    findings=_list_field(structured.get("findings")),
                    reasoning_summary=str(structured.get("reasoning_summary", "")),
                    risks=_list_field(structured.get("risks")),
                    recommendations=_list_field(structured.get("recommendations")),
                    confidence=structured.get("confidence"),
                    observed=_list_field(evidence.get("observed")),
                    inferred=_list_field(evidence.get("inferred")),
                    recommended=_list_field(evidence.get("recommended")),
                    uncertain=_list_field(evidence.get("uncertain")),
                    escalate_to_pro=escalate_to_pro,
                    evidence_packet=evidence_packet,
                    usage=usage,
                ).to_dict()
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    last_error = RouterError(ErrorCode.AUTH, "DeepSeek authentication failed")
                elif exc.code == 429:
                    last_error = RouterError(ErrorCode.RATE_LIMIT, "DeepSeek rate limit exceeded", retryable=True)
                elif exc.code >= 500:
                    last_error = RouterError(ErrorCode.MODEL_ERROR, "DeepSeek provider error", retryable=True)
                else:
                    last_error = RouterError(ErrorCode.MODEL_ERROR, "DeepSeek request rejected")
            except urllib.error.URLError as exc:
                last_error = RouterError(ErrorCode.NETWORK, "DeepSeek network request failed", retryable=True)
            except TimeoutError:
                last_error = RouterError(ErrorCode.TIMEOUT, "DeepSeek request timed out", retryable=True)
            except OSError:
                last_error = RouterError(ErrorCode.NETWORK, "DeepSeek network request failed", retryable=True)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                last_error = RouterError(ErrorCode.INVALID_RESPONSE, "DeepSeek response was not valid JSON")
            if not last_error or not last_error.retryable or attempt >= self.retries:
                break
            time.sleep(0.2 * (2**attempt))
        assert last_error is not None
        raise last_error
