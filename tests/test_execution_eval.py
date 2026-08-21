"""Offline validation for execution and Evidence Packet evaluation assets."""

import importlib.util
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "run_execution_eval", ROOT / "scripts" / "run_execution_eval.py"
)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def test_execution_golden_dataset_has_balanced_16_tasks():
    tasks = evaluation.load_tasks(evaluation.DEFAULT_DATASET)
    assert len(tasks) == 16
    assert Counter(task["category"] for task in tasks) == {
        "FAST": 4,
        "REACT": 4,
        "SPEC": 4,
        "DEEP": 4,
    }


def test_evidence_packet_suite_has_three_real_escalations():
    tasks = evaluation.load_tasks(evaluation.DEFAULT_DATASET)
    escalation = [task for task in tasks if task.get("evidence_escalation")]
    assert len(escalation) == 3
    assert all(task["agent"] == "flash" and task["policy"] == "SPEC" for task in escalation)
    assert all(task.get("pro_checks") for task in escalation)


def test_flash_dry_run_renders_three_ablation_prompts():
    task = evaluation.load_tasks(evaluation.DEFAULT_DATASET)[0]
    records = [evaluation.run_one(task, variant, live=False) for variant in evaluation.ABLATION_VARIANTS]
    assert len({record["prompt_sha256"] for record in records}) == 3
    assert all(record["success"] is None for record in records)
    assert records[0]["adapter_version"] == 0
    assert records[1]["adapter_version"] == 7
    assert records[2]["adapter_version"] == 7
    assert records[1]["added_guidance_chars"] != records[2]["added_guidance_chars"]


def test_pro_contract_and_tuning_ablation_prompts_are_identical():
    task = next(
        task for task in evaluation.load_tasks(evaluation.DEFAULT_DATASET)
        if task["agent"] == "pro"
    )
    contract = evaluation.run_one(task, "contract_only", live=False)
    tuning = evaluation.run_one(task, "contract_tuning", live=False)
    assert contract["prompt_sha256"] == tuning["prompt_sha256"]
    assert contract["guidance_chars"] == tuning["guidance_chars"]


def test_live_records_store_digest_not_provider_output(monkeypatch):
    task = evaluation.load_tasks(evaluation.DEFAULT_DATASET)[0]
    monkeypatch.setattr(
        evaluation.DeepSeekClient,
        "complete",
        lambda self, *args, **kwargs: {
            "status": "completed",
            "summary": "submit post_job",
            "usage": {"input_tokens": 1, "output_tokens": 2, "latency_ms": 3},
            "escalate_to_pro": False,
            "evidence_packet": {},
        },
    )
    record = evaluation.run_one(task, "contract_tuning", live=True)
    assert "result" not in record
    assert len(record["result_sha256"]) == 64
    assert record["environment_check_count"] is None
    assert record["repo_wide_search_count"] is None
    assert record["escalate_to_pro"] is False
    assert record["evidence_packet_complete"] is False


def test_ablation_never_exposes_expected_answer_to_provider():
    task = evaluation.load_tasks(evaluation.DEFAULT_DATASET)[0]
    prompt = evaluation.provider_prompt(task, "contract_tuning")
    for marker in task["checks"].get("contains_all", []):
        if marker not in json.dumps(task["context"], ensure_ascii=False):
            assert marker not in prompt


def test_record_writer_persists_each_record_immediately(tmp_path):
    output = tmp_path / "results.jsonl"
    emit = evaluation._record_writer(output)
    emit({"task_id": "one"})
    assert output.read_text() == '{"task_id":"one"}\n'
    emit({"task_id": "two"})
    assert output.read_text().splitlines() == [
        '{"task_id":"one"}',
        '{"task_id":"two"}',
    ]


def test_automatic_rubric_requires_all_any_and_excludes():
    task = {
        "checks": {
            "contains_all": ["alpha"],
            "contains_any": ["beta", "gamma"],
            "excludes": ["unsafe"],
        }
    }
    assert evaluation.score(task, {"summary": "alpha gamma"}) is True
    assert evaluation.score(task, {"summary": "alpha unsafe gamma"}) is False


def test_main_accepts_evidence_packet_flag(tmp_path):
    output = tmp_path / "evidence.jsonl"
    assert evaluation.main(["--evidence-packet", "--output", str(output)]) == 0
    assert len(output.read_text().splitlines()) == 3


def test_evidence_continuation_passes_only_packet_not_flash_usage(monkeypatch):
    task = next(
        task
        for task in evaluation.load_tasks(evaluation.DEFAULT_DATASET)
        if task.get("evidence_escalation")
    )
    calls = []
    packet = {
        "schema": 1,
        "summary": "bounded",
        "relevant_files": [],
        "observations": [],
        "hypotheses": [],
        "eliminated": [],
        "open_questions": [],
        "recommended_next_step": "continue",
    }

    def complete(client, request, *, context, policy, **kwargs):
        calls.append((client.mode, context))
        if client.mode == "flash":
            return {
                "status": "completed",
                "escalate_to_pro": True,
                "evidence_packet": packet,
                "usage": {"request_id": "do-not-forward"},
            }
        marker = task["pro_checks"]["contains_any"][0]
        return {"status": "completed", "summary": marker, "usage": {}}

    monkeypatch.setattr(evaluation.DeepSeekClient, "complete", complete)
    list(evaluation.run_evidence_packet(task, live=True))

    assert calls[-1][1]["flash_evidence_packet"] == packet
    assert "usage" not in calls[-1][1]["flash_evidence_packet"]
