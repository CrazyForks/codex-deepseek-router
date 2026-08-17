"""Provider-neutral routing and explicit DeepSeek fallback runtime."""

from .router import RouteMode, Router, route

__all__ = ["RouteMode", "Router", "route"]
