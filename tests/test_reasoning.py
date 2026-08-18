"""Reasoning Adapter, asymmetric tuning, and route-contract tests."""

import pytest

from runtime import reasoning


VALID_ROUTES = [
    ("deepseek_flash", "FAST"),
    ("deepseek_flash", "REACT"),
    ("deepseek_flash", "SPEC"),
    ("deepseek_pro", "FAST"),
    ("deepseek_pro", "REACT"),
    ("deepseek_pro", "SPEC"),
    ("deepseek_pro", "DEEP"),
]


@pytest.mark.parametrize(("agent_type", "policy"), VALID_ROUTES)
def test_valid_route_matrix(agent_type, policy):
    reasoning.validate_route_contract(agent_type, policy)


def test_flash_deep_is_rejected_with_actionable_error():
    with pytest.raises(reasoning.RouteContractError, match="requires deepseek_pro"):
        reasoning.validate_route_contract("deepseek_flash", "DEEP")


def test_adapter_has_one_version_and_four_parent_selected_policies():
    assert reasoning.REASONING_ADAPTER_VERSION == 1
    assert set(reasoning.VALID_ROUTE_MATRIX) == {"deepseek_flash", "deepseek_pro"}
    assert reasoning.POLICIES == {"FAST", "REACT", "SPEC", "DEEP"}
    assert "weak" not in reasoning.POLICIES
    assert "mixed" not in reasoning.POLICIES


def test_flash_react_is_read_only_proposal_contract():
    contract = reasoning.get_policy_contract("deepseek_flash", "REACT")
    assert "read-only" in contract
    assert "Do not modify" in contract
    assert "suggested tests" in contract


def test_flash_spec_lite_escalates_deep_work_with_evidence():
    contract = reasoning.get_policy_contract("deepseek_flash", "SPEC")
    assert "ESCALATE_TO_PRO" in contract
    assert "Evidence Packet" in contract
    assert "concurrency" in contract


def test_deep_is_information_driven_and_has_decision_closure():
    contract = reasoning.get_policy_contract("deepseek_pro", "DEEP")
    stop = reasoning.get_stop_condition("deepseek_pro", "DEEP")
    assert "information-driven" in contract
    assert "distinguishes" in contract
    assert "distinguish" in stop
    assert "without changing the decision" in stop


def test_model_tuning_is_deliberately_asymmetric():
    flash = reasoning.get_model_tuning("deepseek_flash", "FAST")
    pro = reasoning.get_model_tuning("deepseek_pro", "REACT")
    for marker in ("confirmed reads", "environment ceremony", "unbounded repo-wide"):
        assert marker in flash
    assert pro == ""
    assert reasoning.PRO_TUNING_MINIMAL == ""


@pytest.mark.parametrize(("agent_type", "policy"), VALID_ROUTES)
def test_rendered_adapter_is_short_and_has_policy_and_stop(agent_type, policy):
    rendered = reasoning.render_reasoning_context(agent_type, policy)
    assert f"POLICY\n{policy}" in rendered
    assert "POLICY EXECUTION CONTRACT" in rendered
    assert "CONVERGENCE / STOP CONDITION" in rendered
    assert len(rendered) < 1_500
    assert "chain-of-thought" not in rendered.lower()
    assert "ignore all previous" not in rendered.lower()


def test_flash_tuning_is_omitted_from_contract_only_ablation():
    full = reasoning.render_reasoning_context("deepseek_flash", "FAST")
    contract_only = reasoning.render_reasoning_context(
        "deepseek_flash", "FAST", include_model_tuning=False
    )
    assert "MODEL-SPECIFIC TUNING" in full
    assert "MODEL-SPECIFIC TUNING" not in contract_only
    assert "POLICY EXECUTION CONTRACT" in contract_only


def test_pro_contract_only_and_contract_tuning_are_identical():
    full = reasoning.render_reasoning_context("deepseek_pro", "SPEC")
    contract_only = reasoning.render_reasoning_context(
        "deepseek_pro", "SPEC", include_model_tuning=False
    )
    assert full == contract_only
    assert "MODEL-SPECIFIC TUNING" not in full
    assert "confirmed reads" not in full
    assert "environment ceremony" not in full


def test_fallback_render_declares_capability_boundary():
    rendered = reasoning.render_reasoning_context("deepseek_pro", "REACT", fallback=True)
    assert "explicit text-only fallback" in rendered
    assert "does not provide the native Codex subagent tool environment" in rendered
