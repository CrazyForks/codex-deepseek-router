"""Dependency-light persistent provider profile shared by manager and runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlsplit


SETTINGS_SCHEMA_VERSION = 1
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_FLASH_MODEL = "deepseek-v4-flash"
DEFAULT_PRO_MODEL = "deepseek-v4-pro"
WIRE_API = "responses"


class ProviderConfigError(ValueError):
    pass


def normalize_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderConfigError("base URL must be a non-empty string")
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProviderConfigError("base URL must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ProviderConfigError("base URL must not contain embedded credentials")
    if parsed.query or parsed.fragment:
        raise ProviderConfigError("base URL must not contain a query or fragment")
    if parsed.path.rstrip("/").endswith("/responses"):
        raise ProviderConfigError("base URL must not include /responses")
    return normalized


def validate_model_id(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderConfigError(f"{field_name} must be a non-empty string")
    if any(ord(character) < 32 for character in value):
        raise ProviderConfigError(f"{field_name} contains a control character")
    return value


@dataclass(frozen=True)
class RouterConfig:
    base_url: str = DEFAULT_BASE_URL
    flash_model: str = DEFAULT_FLASH_MODEL
    pro_model: str = DEFAULT_PRO_MODEL
    wire_api: str = WIRE_API

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", normalize_base_url(self.base_url))
        object.__setattr__(self, "flash_model", validate_model_id(self.flash_model, "flash model ID"))
        object.__setattr__(self, "pro_model", validate_model_id(self.pro_model, "pro model ID"))
        if self.wire_api != WIRE_API:
            raise ProviderConfigError(f"wire_api must remain {WIRE_API!r}")

    def model_for(self, mode: str) -> str:
        if mode == "flash":
            return self.flash_model
        if mode == "pro":
            return self.pro_model
        raise ProviderConfigError(f"unknown router mode: {mode}")


def settings_payload(config: RouterConfig) -> Dict[str, Any]:
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "base_url": config.base_url,
        "flash_model": config.flash_model,
        "pro_model": config.pro_model,
    }


def load_router_config(settings_path: Path) -> Optional[RouterConfig]:
    if not settings_path.is_file():
        return None
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderConfigError(f"could not read router settings: {exc}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != SETTINGS_SCHEMA_VERSION:
        raise ProviderConfigError("router settings have an unsupported schema")
    try:
        return RouterConfig(
            base_url=payload["base_url"],
            flash_model=payload["flash_model"],
            pro_model=payload["pro_model"],
        )
    except KeyError as exc:
        raise ProviderConfigError(f"router settings are missing {exc.args[0]}") from exc
