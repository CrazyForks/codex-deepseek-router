"""Acceptance-driven assignment and quality-regression contract tests."""

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILL = (ROOT / "skills" / "use-deepseek-router" / "SKILL.md").read_text(encoding="utf-8")
REASONING = (ROOT / "runtime" / "reasoning.py").read_text(encoding="utf-8")


def test_skill_requires_acceptance_sections_and_parent_gate():
    for marker in (
        "ACCEPTANCE CRITERIA",
        "VERIFICATION OWNER",
        "STOPPING CONDITION",
        "Parent acceptance gate",
        "UNVERIFIED",
        "at most one",
        "MATERIAL GAPS",
        "DO NOT CHANGE",
        "deepseek_pro + REACT",
    ):
        assert marker in SKILL


def test_acceptance_contract_does_not_add_policy_or_transport_schema():
    assert "QUALITY_REFINEMENT" not in SKILL
    assert "AcceptanceProfile" not in SKILL
    assert "refinement_count" not in SKILL
    assert "acceptance_profile" not in SKILL
    assert "ACCEPTANCE CRITERIA" not in REASONING


def test_quality_regression_cases_keep_parent_visual_ownership():
    cases = json.loads((ROOT / "eval" / "native-quality-tasks.json").read_text(encoding="utf-8"))
    assert {case["task_id"] for case in cases} == {
        "visual-black-hole",
        "responsive-ui",
        "interactive-canvas",
    }
    for case in cases:
        assert case["verification_owner"] == "SHARED"
        assert len(case["acceptance_criteria"]) >= 4
    black_hole = next(case for case in cases if case["task_id"] == "visual-black-hole")
    criteria = " ".join(black_hole["acceptance_criteria"])
    for marker in ("shadow", "lensing", "Doppler", "disk"):
        assert marker.lower() in criteria.lower()
