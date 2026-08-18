#!/usr/bin/env python3
"""Run Current/Contract/Tuning ablations and Evidence Packet evaluations."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import sys
from collections import Counter
from typing import Any, Dict, Iterable, List

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.client import DeepSeekClient, build_fallback_prompt  # noqa: E402
from runtime.context import TaskContext, sanitize_context  # noqa: E402
from runtime.protocol import RouterError  # noqa: E402
from runtime.reasoning import (  # noqa: E402
    REASONING_ADAPTER_VERSION,
    build_reasoning_context,
)


DEFAULT_DATASET = ROOT / "eval" / "execution-golden-tasks.json"
BASELINE_FIXTURE = ROOT / "eval" / "baseline-3aa3bf2.json"
ABLATION_VARIANTS = ("current", "contract_only", "contract_tuning")


def load_tasks(path: pathlib.Path) -> List[Dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or len(value) != 16:
        raise ValueError("execution dataset must contain exactly 16 tasks")
    ids = set()
    categories = Counter()
    for task in value:
        required = {"task_id", "category", "agent", "policy", "task", "context", "checks"}
        if not isinstance(task, dict) or not required <= set(task):
            raise ValueError("each execution task must contain the required fields")
        if task["task_id"] in ids:
            raise ValueError(f"duplicate task id: {task['task_id']}")
        ids.add(task["task_id"])
        categories[task["category"]] += 1
    if categories != Counter({"FAST": 4, "REACT": 4, "SPEC": 4, "DEEP": 4}):
        raise ValueError("execution dataset must contain four tasks per policy")
    return value


def load_baseline() -> Dict[str, Any]:
    return json.loads(BASELINE_FIXTURE.read_text(encoding="utf-8"))


def score(task: Dict[str, Any], result: Dict[str, Any]) -> bool:
    text = json.dumps(result, ensure_ascii=False).lower()
    checks = task["checks"]
    all_ok = all(str(marker).lower() in text for marker in checks.get("contains_all", []))
    any_values = checks.get("contains_any", [])
    any_ok = not any_values or any(str(marker).lower() in text for marker in any_values)
    excludes_ok = all(str(marker).lower() not in text for marker in checks.get("excludes", []))
    return all_ok and any_ok and excludes_ok


def experiment_assignment(task: Dict[str, Any], variant: str) -> str:
    if variant not in ABLATION_VARIANTS:
        raise ValueError(f"unknown ablation variant: {variant}")
    evidence = json.dumps(task["context"], ensure_ascii=False, sort_keys=True, indent=2)
    assignment = "\n".join(
        (
            "BEGIN PARENT ASSIGNMENT",
            task["task"],
            "END PARENT ASSIGNMENT",
            "",
            "BEGIN SUPPLIED TASK EVIDENCE",
            evidence,
            "END SUPPLIED TASK EVIDENCE",
        )
    )
    if variant == "current":
        baseline = load_baseline()
        instructions = baseline[
            "flash_developer_instructions" if task["agent"] == "flash" else "pro_developer_instructions"
        ]
        return "\n\n".join(
            (
                "CURRENT STATIC AGENT INSTRUCTIONS\n" + instructions,
                assignment,
                "REASONING_POLICY: " + task["policy"],
            )
        )
    agent_type = "deepseek_flash" if task["agent"] == "flash" else "deepseek_pro"
    reasoning = build_reasoning_context(agent_type, task["policy"])
    return assignment + "\n\n" + reasoning.render(
        include_model_tuning=variant == "contract_tuning",
        fallback=True,
    )


def provider_prompt(task: Dict[str, Any], variant: str) -> str:
    """Exact prompt sent in eval: one neutral fallback wrapper around the variant."""
    experiment = experiment_assignment(task, variant)
    context = TaskContext(task=experiment, relevant_files={"context": "{}"}).render(task["agent"])
    return build_fallback_prompt(
        task["agent"], task["policy"], context, guidance_variant="current"
    )


def public_usage(result: Dict[str, Any]) -> Dict[str, Any]:
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    allowed = {
        "input_tokens", "output_tokens", "total_tokens", "latency_ms", "model", "request_id"
    }
    return {key: value for key, value in usage.items() if key in allowed}


def run_one(
    task: Dict[str, Any], variant: str, live: bool, repetition: int = 1
) -> Dict[str, Any]:
    experiment = experiment_assignment(task, variant)
    prompt = provider_prompt(task, variant)
    current_chars = len(experiment_assignment(task, "current"))
    guidance_delta = len(experiment) - current_chars
    base = {
        "task_id": task["task_id"],
        "variant": variant,
        "repetition": repetition,
        "agent": task["agent"],
        "policy": task["policy"],
        "adapter_version": 0 if variant == "current" else REASONING_ADAPTER_VERSION,
        "success": None,
        "tool_calls": None,
        "reads": None,
        "duplicate_read_count": None,
        "environment_check_count": None,
        "repo_wide_search_count": None,
        "unbounded_search_count": None,
        "time_to_first_edit_ms": None,
        "time_to_answer_ms": None,
        "latency_ms": None,
        "deepseek_input_tokens": None,
        "deepseek_output_tokens": None,
        "parent_rework": None,
        "guidance_chars": len(experiment),
        "added_guidance_chars": guidance_delta,
        "estimated_added_prompt_tokens": math.ceil(guidance_delta / 4),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "notes": "dry-run; tool behavior metrics require a native Codex trace" if not live else "",
    }
    if not live:
        return base
    try:
        result = DeepSeekClient(task["agent"]).complete(
            experiment,
            context={},
            policy=task["policy"],
            _reasoning_variant="current",
        )
    except RouterError as error:
        return {**base, "success": False, "notes": f"provider error: {error.code.value}"}
    usage = public_usage(result)
    return {
        **base,
        "success": score(task, result),
        "time_to_answer_ms": usage.get("latency_ms"),
        "latency_ms": usage.get("latency_ms"),
        "deepseek_input_tokens": usage.get("input_tokens"),
        "deepseek_output_tokens": usage.get("output_tokens"),
        "notes": "automatic content rubric; no native tool trace",
        "result_sha256": hashlib.sha256(
            json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


def run_evidence_packet(task: Dict[str, Any], live: bool) -> Iterable[Dict[str, Any]]:
    if not live:
        yield {
            "task_id": task["task_id"],
            "variant": "contract_tuning",
            "adapter_version": REASONING_ADAPTER_VERSION,
            "route": "flash_to_pro",
            "success": None,
            "duplicate_read_count": None,
            "parent_rework": None,
            "notes": "dry-run; live mode compares direct Pro with Flash then Pro",
        }
        return
    flash = DeepSeekClient("flash").complete(
        task["task"], context=task["context"], policy="SPEC"
    )
    direct = DeepSeekClient("pro").complete(
        task["task"], context=task["context"], policy="SPEC"
    )
    continued_context = dict(task["context"])
    continued_context["flash_evidence_packet"] = flash
    continued = DeepSeekClient("pro").complete(
        "Continue from the supplied Flash Evidence Packet. Do not rescan by default. " + task["task"],
        context=continued_context,
        policy="SPEC",
    )
    for route, result in (("flash", flash), ("direct_pro", direct), ("flash_to_pro", continued)):
        yield {
            "task_id": task["task_id"],
            "variant": "contract_tuning",
            "adapter_version": REASONING_ADAPTER_VERSION,
            "route": route,
            "success": score(task, result) if route == "flash" else result.get("status") == "completed",
            "usage": public_usage(result),
            "duplicate_read_count": None,
            "parent_rework": None,
            "result_sha256": hashlib.sha256(
                json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "notes": "public provider usage only; native read metrics require a Codex trace",
        }


def _write_records(records: List[Dict[str, Any]], output: pathlib.Path = None) -> None:
    rendered = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=pathlib.Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--variant", choices=(*ABLATION_VARIANTS, "all"), default="all"
    )
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--smoke", action="store_true", help="select two tasks per policy")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--live", action="store_true", help="perform billable provider requests")
    parser.add_argument("--evidence-packet", action="store_true")
    parser.add_argument("--output", type=pathlib.Path, help="write JSONL records to this path")
    args = parser.parse_args(argv)
    if args.repetitions < 1:
        raise ValueError("repetitions must be at least 1")

    tasks = load_tasks(args.dataset)
    if args.task_id:
        selected = set(args.task_id)
        tasks = [task for task in tasks if task["task_id"] in selected]
        if len(tasks) != len(selected):
            raise ValueError("one or more requested task ids do not exist")
    elif args.smoke:
        by_policy = Counter()
        smoke = []
        for task in tasks:
            if by_policy[task["category"]] < 2:
                smoke.append(task)
                by_policy[task["category"]] += 1
        tasks = smoke

    records = []
    if args.evidence_packet:
        tasks = [task for task in tasks if task.get("evidence_escalation")]
        for task in tasks:
            records.extend(run_evidence_packet(task, args.live))
    else:
        variants = ABLATION_VARIANTS if args.variant == "all" else (args.variant,)
        for repetition in range(1, args.repetitions + 1):
            for task in tasks:
                for variant in variants:
                    records.append(run_one(task, variant, args.live, repetition))
    _write_records(records, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
