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


MODELS = {"flash": "deepseek-v4-flash", "pro": "deepseek-v4-pro"}
BASE_URL = "https://api.deepseek.com"


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
        return {"summary": candidate, "reasoning_summary": "", "findings": []}
    return value if isinstance(value, dict) else {"summary": candidate}


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

    def complete(self, task: str, *, context: Optional[Mapping[str, Any]] = None, cancel_event: Any = None) -> Dict[str, Any]:
        key = get_api_key()
        if not key:
            raise RouterError(ErrorCode.AUTH, "DeepSeek API key is not configured")
        rendered = TaskContext(task=task, relevant_files={"context": sanitize_context(context or {})}).render(self.mode)
        prompt = (
            "Return JSON only with summary, findings, reasoning_summary, risks, recommendations, "
            "confidence, and evidence fields observed/inferred/recommended/uncertain. Do not expose chain-of-thought.\n\n"
            + rendered
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
                usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
                usage = {**usage, "model": MODELS[self.mode], "latency_ms": round((time.monotonic() - started) * 1000), "request_id": request_id}
                return RoutingResult(
                    mode=self.mode,
                    model=MODELS[self.mode],
                    status="completed",
                    summary=str(structured.get("summary", "")),
                    findings=list(structured.get("findings") or []),
                    reasoning_summary=str(structured.get("reasoning_summary", "")),
                    risks=list(structured.get("risks") or []),
                    recommendations=list(structured.get("recommendations") or []),
                    confidence=structured.get("confidence"),
                    observed=list((structured.get("evidence") or {}).get("observed") or []),
                    inferred=list((structured.get("evidence") or {}).get("inferred") or []),
                    recommended=list((structured.get("evidence") or {}).get("recommended") or []),
                    uncertain=list((structured.get("evidence") or {}).get("uncertain") or []),
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
