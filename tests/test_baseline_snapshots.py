"""Frozen pre-Adapter behavior from main commit 3aa3bf2."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_frozen_main_baseline_is_complete():
    fixture = json.loads((ROOT / "eval" / "baseline-3aa3bf2.json").read_text())
    assert fixture["source_commit"] == "3aa3bf2b796369bc1a8d035cfcf6e7f26985b859"
    assert fixture["pytest"] == {
        "passed": 141,
        "skipped": 21,
        "failed": 0,
        "platform": "macOS, Python 3.9.6",
    }
    assert "REASONING_POLICY: SPEC" in fixture["native_child_context"]
    assert "Do not expose chain-of-thought" in fixture["fallback_prompt"]
    assert "For investigation:" in fixture["pro_developer_instructions"]
    assert "Prefer fast evidence gathering" in fixture["flash_developer_instructions"]
    assert fixture["routing_eval"]["flash_advantage"]["tasks"] == 20
    assert fixture["transport_invariants"] == [
        "per-role pending filename",
        "claim-first atomic rename",
        "one-shot consume",
        "300 second default TTL",
        "malformed quarantine",
        "per-role non-blocking lock",
    ]
