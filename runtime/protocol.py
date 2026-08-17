"""Stable, non-chain-of-thought result and error contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ErrorCode(str, Enum):
    AUTH = "AUTH"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    MODEL_ERROR = "MODEL_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    CONFIGURATION = "CONFIGURATION"
    CANCELLED = "CANCELLED"


class RequestState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class RouterError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class RoutingResult:
    mode: str
    model: str
    status: str
    summary: str = ""
    findings: List[Dict[str, Any]] = field(default_factory=list)
    reasoning_summary: str = ""
    risks: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    confidence: Optional[float] = None
    observed: List[str] = field(default_factory=list)
    inferred: List[str] = field(default_factory=list)
    recommended: List[str] = field(default_factory=list)
    uncertain: List[str] = field(default_factory=list)
    error: Optional[Dict[str, Any]] = None
    usage: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "model": self.model,
            "status": self.status,
            "summary": self.summary,
            "findings": self.findings,
            "reasoning_summary": self.reasoning_summary,
            "risks": self.risks,
            "recommendations": self.recommendations,
            "confidence": self.confidence,
            "evidence": {
                "observed": self.observed,
                "inferred": self.inferred,
                "recommended": self.recommended,
                "uncertain": self.uncertain,
            },
            "error": self.error,
            "usage": self.usage,
        }
