"""Router contract tests: modality gate, visual/evidence packets, routing
decisions, transport selection, dual-model catalog, JSON schemas."""

import json
from pathlib import Path

import pytest

import codex_deepseek_router as manager
import plaintext_handoff as handoff

PACKAGE = Path(__file__).resolve().parents[1] / "codex-deepseek-router"
SCHEMAS = PACKAGE / "schemas"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# modality gate
# ---------------------------------------------------------------------------


def test_modality_values():
    assert manager.Modality.TEXT_ONLY == "TEXT_ONLY"
    assert manager.Modality.VISION_TRANSLATABLE == "VISION_TRANSLATABLE"
    assert manager.Modality.VISION_CRITICAL == "VISION_CRITICAL"


def test_deepseek_allowed():
    assert manager.deepseek_allowed("TEXT_ONLY") is True
    assert manager.deepseek_allowed(manager.Modality.TEXT_ONLY) is True
    assert manager.deepseek_allowed("VISION_TRANSLATABLE") is True
    assert manager.deepseek_allowed("VISION_CRITICAL") is False
    assert manager.deepseek_allowed(None) is False
    assert manager.deepseek_allowed("bogus") is False


# ---------------------------------------------------------------------------
# visual context packet
# ---------------------------------------------------------------------------


def test_visual_context_marks_parent_only():
    packet = manager.VisualContext(
        source_type="screenshot",
        user_goal="fix UI",
    ).to_dict()
    assert packet["source_visibility"] == "parent_only"
    assert packet["schema"] == 1
    assert packet["observations"] == []


def test_visual_context_full_packet():
    packet = manager.VisualContext(
        source_type="screenshot",
        user_goal="Fix the header alignment.",
        observations=["viewport approximately 1440 px"],
        visible_text=["Patients", "Save"],
        relationships=["button center aligns with title vertically"],
        uncertainties=["exact margin cannot be determined"],
    ).to_dict()
    assert packet["visible_text"] == ["Patients", "Save"]
    assert len(packet["uncertainties"]) == 1


# ---------------------------------------------------------------------------
# evidence packet
# ---------------------------------------------------------------------------


def test_evidence_packet_shape():
    packet = manager.EvidencePacket(
        summary="narrowed to two hypotheses",
        relevant_files=["src/a.rs"],
        observations=["both paths mutate shared state"],
        hypotheses=["h1", "h2"],
        eliminated=["h3"],
        open_questions=["lock ordering"],
        recommended_next_step="trace lock ordering in DEEP pass",
    ).to_dict()
    assert packet["schema"] == 1
    for key in (
        "summary",
        "relevant_files",
        "observations",
        "hypotheses",
        "eliminated",
        "open_questions",
        "recommended_next_step",
    ):
        assert key in packet


# ---------------------------------------------------------------------------
# routing decision / policies / transport
# ---------------------------------------------------------------------------


def test_routing_decision_contract():
    decision = manager.RoutingDecision(
        agent="FLASH",
        policy="FAST",
        modality="TEXT_ONLY",
        reason="repository search",
    )
    assert decision.agent in {"NONE", "FLASH", "PRO"}
    assert decision.policy in manager.POLICIES
    assert decision.modality == "TEXT_ONLY"


def test_policies_contract():
    assert manager.POLICIES == ("FAST", "REACT", "SPEC", "DEEP")


def test_choose_transport_prefers_native():
    assert manager.choose_transport(True, True) == manager.TransportMode.NATIVE
    assert manager.choose_transport(False, True) == manager.TransportMode.PLAINTEXT_HOOK
    assert manager.choose_transport(False, False) == manager.TransportMode.LEGACY_V1


def test_v1_default_transport_is_plaintext_hook():
    manifest = manager.default_manifest("gpt-5.6-test", "openai")
    assert manifest["transport_mode"] == manager.TransportMode.PLAINTEXT_HOOK.value


# ---------------------------------------------------------------------------
# dual-model catalog
# ---------------------------------------------------------------------------


def test_catalog_registers_both_models_and_roles():
    payload = manager.catalog_payload()
    by_slug = {item["slug"]: item for item in payload["models"]}
    assert set(by_slug) == {"deepseek-v4-flash", "deepseek-v4-pro"}
    assert by_slug["deepseek-v4-flash"]["router_roles"] == ["deepseek_flash"]
    assert by_slug["deepseek-v4-pro"]["router_roles"] == ["deepseek_pro"]


# ---------------------------------------------------------------------------
# envelope policy/modality coherence with the router contracts
# ---------------------------------------------------------------------------


def test_handoff_validates_router_policies():
    envelope = handoff.new_envelope(
        agent_type="deepseek_flash", assignment="x", policy="DEEP", modality="TEXT_ONLY"
    )
    assert handoff.validate_envelope(envelope)[0]["policy"] == "DEEP"


def test_handoff_rejects_unknown_policy():
    with pytest.raises(handoff.EnvelopeError):
        handoff.new_envelope(
            agent_type="deepseek_flash", assignment="x", policy="HYPER", modality="TEXT_ONLY"
        )


# ---------------------------------------------------------------------------
# JSON schemas
# ---------------------------------------------------------------------------


def _check_schema(schema, instance, path="$"):
    if "const" in schema:
        assert instance == schema["const"], f"{path}: const {schema['const']} violated"
    if "enum" in schema:
        assert instance in schema["enum"], f"{path}: enum violated"
    schema_type = schema.get("type")
    if schema_type == "object":
        assert isinstance(instance, dict), f"{path}: expected object"
        if schema.get("additionalProperties") is False:
            assert set(instance) <= set(schema.get("properties", {})), f"{path}: unknown keys"
        for required in schema.get("required", []):
            assert required in instance, f"{path}: missing required {required}"
        for key, value in instance.items():
            prop = schema.get("properties", {}).get(key)
            if prop:
                _check_schema(prop, value, f"{path}/{key}")
    elif schema_type == "array":
        assert isinstance(instance, list), f"{path}: expected array"
        for index, item in enumerate(instance):
            _check_schema(schema["items"], item, f"{path}/{index}")
    elif schema_type == "string":
        assert isinstance(instance, str), f"{path}: expected string"
        if "minLength" in schema:
            assert len(instance) >= schema["minLength"], f"{path}: too short"
        if "maxLength" in schema:
            assert len(instance) <= schema["maxLength"], f"{path}: too long"
    elif schema_type == "integer":
        assert isinstance(instance, int) and not isinstance(instance, bool), f"{path}: expected integer"


def test_assignment_envelope_fixture_matches_schema():
    schema = json.loads((SCHEMAS / "assignment-envelope.schema.json").read_text())
    envelope = handoff.new_envelope(
        agent_type="deepseek_pro",
        assignment="trace the race",
        policy="SPEC",
        modality="VISION_TRANSLATABLE",
        visual_context={"schema": 1, "source_type": "screenshot", "user_goal": "fix"},
    )
    _check_schema(schema, envelope)


def test_visual_context_fixture_matches_schema():
    schema = json.loads((SCHEMAS / "visual-context.schema.json").read_text())
    fixture = json.loads((FIXTURES / "visual-context-example.json").read_text())
    _check_schema(schema, fixture)
    assert fixture["source_visibility"] == "parent_only"


def test_evidence_packet_fixture_matches_schema():
    schema = json.loads((SCHEMAS / "evidence-packet.schema.json").read_text())
    fixture = json.loads((FIXTURES / "evidence-packet-example.json").read_text())
    _check_schema(schema, fixture)


def test_schema_files_are_valid_json():
    for name in (
        "assignment-envelope.schema.json",
        "visual-context.schema.json",
        "evidence-packet.schema.json",
    ):
        loaded = json.loads((SCHEMAS / name).read_text())
        assert loaded["$schema"].startswith("http://json-schema.org/draft-07")


# ---------------------------------------------------------------------------
# runtime skill content sanity
# ---------------------------------------------------------------------------


def test_runtime_skill_documents_the_dispatch_steps():
    text = (PACKAGE / "skills" / "use-deepseek-router" / "SKILL.md").read_text()
    for marker in (
        "TEXT_ONLY",
        "VISION_TRANSLATABLE",
        "VISION_CRITICAL",
        "deepseek_flash",
        "deepseek_pro",
        "FAST",
        "REACT",
        "SPEC",
        "DEEP",
        "ESCALATE_TO_PRO",
        "NEED_VISUAL_CLARIFICATION",
        'fork_turns="none"',
    ):
        assert marker in text


# ---------------------------------------------------------------------------
# routing eval datasets (Epic 21 fixtures)
# ---------------------------------------------------------------------------

EVAL_FILES = {
    "eval-flash-advantage.json": ("A", 20, "FLASH"),
    "eval-pro-advantage.json": ("B", 20, "PRO"),
    "eval-multimodal.json": ("C", 10, None),
    "eval-no-delegation.json": ("D", 10, "NONE"),
}


def test_eval_datasets_are_complete_and_consistent():
    for filename, (group, expected_size, expected_agent) in EVAL_FILES.items():
        payload = json.loads((FIXTURES / filename).read_text())
        assert payload["group"] == group
        tasks = payload["tasks"]
        assert len(tasks) == expected_size, filename
        ids = [task["id"] for task in tasks]
        assert len(set(ids)) == len(ids), f"duplicate ids in {filename}"
        for task in tasks:
            assert task["expected_agent"] in {"FLASH", "PRO", "NONE"}
            assert task["expected_policy"] in {"FAST", "REACT", "SPEC", "DEEP"}
            assert task["expected_modality"] in {
                "TEXT_ONLY",
                "VISION_TRANSLATABLE",
                "VISION_CRITICAL",
            }
            if expected_agent:
                assert task["expected_agent"] == expected_agent, f"{filename}/{task['id']}"


def test_multimodal_dataset_never_routes_visual_critical_to_deepseek():
    payload = json.loads((FIXTURES / "eval-multimodal.json").read_text())
    for task in payload["tasks"]:
        if task["expected_modality"] == "VISION_CRITICAL":
            assert task["expected_agent"] == "NONE", task["id"]
