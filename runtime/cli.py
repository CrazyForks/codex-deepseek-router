"""CLI for explicit routing when automatic Plugin Hooks are unavailable."""

from __future__ import annotations

import argparse
import json
import sys

if __package__ in (None, ""):
    import pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from runtime.protocol import RouterError
    from runtime.router import route
else:
    from .protocol import RouterError
    from .router import route


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("auto", "flash", "pro", "codex"), default="auto")
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or not isinstance(payload.get("task"), str):
            raise ValueError("input must be an object with a string task")
        policy = payload.get("policy")
        if policy is not None and not isinstance(policy, str):
            raise ValueError("policy must be FAST, REACT, SPEC, or DEEP when provided")
        result = route(
            payload["task"], payload.get("context") or {}, args.mode, policy=policy
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except RouterError as exc:
        print(json.dumps({"status": "failed", "error": {"code": exc.code.value, "message": str(exc), "retryable": exc.retryable}}, ensure_ascii=False))
        return 2
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error": {"code": "CONFIGURATION", "message": str(exc)}}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
