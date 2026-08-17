"""Model selection independent of Hooks and native child transport."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from .protocol import RouterError, ErrorCode


class RouteMode(str, Enum):
    AUTO = "auto"
    FLASH = "flash"
    PRO = "pro"
    CODEX = "codex"


@dataclass(frozen=True)
class Decision:
    mode: RouteMode
    reason: str
    score: float


def _number(context: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        value = context.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def choose(mode: str, task: str, context: Optional[Mapping[str, Any]] = None) -> Decision:
    requested = RouteMode(mode.lower())
    if requested in {RouteMode.FLASH, RouteMode.PRO, RouteMode.CODEX}:
        return Decision(requested, "explicit model selection", 1.0)
    if requested is not RouteMode.AUTO:
        raise RouterError(ErrorCode.CONFIGURATION, f"Unsupported route mode: {mode}")
    metadata = context or {}
    files = _number(metadata, "file_count", "number_of_files")
    chars = _number(metadata, "context_size", "characters", "token_count")
    depth = _number(metadata, "reasoning_depth", "complexity")
    score = min(1.0, (files / 12.0) * 0.25 + (chars / 240_000.0) * 0.35 + (depth / 10.0) * 0.40)
    if not task.strip():
        return Decision(RouteMode.CODEX, "empty task stays with parent", 0.0)
    if score >= 0.55:
        return Decision(RouteMode.PRO, "context and reasoning demand exceed Flash budget", score)
    return Decision(RouteMode.FLASH, "bounded text task fits the Flash budget", score)


@dataclass
class Router:
    client_factory: Any

    def route(self, task: str, context: Optional[Mapping[str, Any]] = None, mode: str = "auto", **kwargs: Any):
        decision = choose(mode, task, context)
        if decision.mode is RouteMode.CODEX:
            return {"mode": "codex", "status": "parent_required", "reason": decision.reason}
        client = self.client_factory(decision.mode.value)
        return client.complete(task, context=context or {}, **kwargs)


def route(task: str, context: Optional[Mapping[str, Any]] = None, mode: str = "auto", **kwargs: Any):
    from .client import DeepSeekClient

    return Router(lambda selected: DeepSeekClient(selected)).route(task, context, mode, **kwargs)
