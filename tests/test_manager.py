"""Manager tests: atomic write, paths, agents, catalog, transactions, lifecycle.

Lifecycle tests run against a fake codex home and never touch the real
~/.codex or the real system credential store.
"""

import json
import threading
import uuid

import pytest

import codex_deepseek_router as manager


# ---------------------------------------------------------------------------
# atomic write / hashing
# ---------------------------------------------------------------------------


def test_atomic_write(tmp_path):
    target = tmp_path / "x.txt"
    manager.atomic_write(target, b"hello")
    assert target.read_bytes() == b"hello"


def test_atomic_write_creates_parents(tmp_path):
    target = tmp_path / "a" / "b" / "x.txt"
    manager.atomic_write(target, b"data")
    assert target.read_bytes() == b"data"


def test_sha256_text_file_normalizes_line_endings(tmp_path):
    path = tmp_path / "f.txt"
    path.write_bytes(b"a\r\nb\r\n")
    crlf = manager.sha256_text_file(path)
    path.write_bytes(b"a\nb\n")
    lf = manager.sha256_text_file(path)
    assert crlf == lf


# ---------------------------------------------------------------------------
# paths / agent specs
# ---------------------------------------------------------------------------


def test_agents_are_independent(tmp_path):
    paths = manager.Paths(tmp_path)
    assert paths.flash_agent != paths.pro_agent
    assert paths.flash_agent.name == "deepseek-flash.toml"
    assert paths.pro_agent.name == "deepseek-pro.toml"


def test_supported_roles_cover_both_models():
    assert manager.SUPPORTED_ROLES == {
        manager.FLASH_ROLE: manager.FLASH_MODEL,
        manager.PRO_ROLE: manager.PRO_MODEL,
    }
    assert manager.VALID_AGENTS == {manager.FLASH_ROLE, manager.PRO_ROLE}


def test_agent_toml_dual_models_and_no_reasoning_effort():
    flash = manager.agent_toml_text(manager.AGENT_SPECS[manager.FLASH_ROLE])
    pro = manager.agent_toml_text(manager.AGENT_SPECS[manager.PRO_ROLE])
    assert 'name = "deepseek_flash"' in flash
    assert 'name = "deepseek_pro"' in pro
    assert 'model = "deepseek-v4-flash"' in flash
    assert 'model = "deepseek-v4-pro"' in pro
    assert 'model_provider = "deepseek"' in flash and 'model_provider = "deepseek"' in pro
    assert "[model_providers.deepseek]" in flash and "[model_providers.deepseek]" in pro
    assert 'wire_api = "responses"' in flash
    assert "model_reasoning_effort" not in flash and "model_reasoning_effort" not in pro
    assert 'sandbox_mode = "read-only"' in flash
    assert 'sandbox_mode = "workspace-write"' in pro
    auth_lines = ("env_key" in flash) or ("[model_providers.deepseek.auth]" in flash)
    assert auth_lines


def test_agent_toml_never_contains_api_key_value():
    for role in manager.SUPPORTED_ROLES:
        text = manager.agent_toml_text(manager.AGENT_SPECS[role])
        assert "sk-" not in text


def test_catalog_registers_both_models():
    payload = manager.catalog_payload()
    slugs = {item["slug"] for item in payload["models"]}
    assert slugs == {manager.FLASH_MODEL, manager.PRO_MODEL}
    for item in payload["models"]:
        assert item["model_provider"] == manager.PROVIDER


def test_catalog_is_stable_between_calls():
    assert manager.catalog_payload() == manager.catalog_payload()


# ---------------------------------------------------------------------------
# TOML access
# ---------------------------------------------------------------------------


def test_toml_top_level_string(tmp_home):
    text = (tmp_home / "config.toml").read_text()
    assert manager.toml_get_top_level_string(text, "model") == "gpt-5.6-test"
    assert manager.toml_get_top_level_string(text, "model_provider") == "openai"
    assert manager.toml_get_top_level_string(text, "missing") is None


def test_toml_has_table():
    assert manager.toml_has_table('[hooks]\nstate = "x"\n', "hooks")
    assert manager.toml_has_table('[model_providers.deepseek]\nname = "x"\n', "model_providers.deepseek")
    assert not manager.toml_has_table('model = "x"\n', "hooks")
    assert not manager.toml_has_table('[hooks-other]\nx = 1\n', "hooks")


# ---------------------------------------------------------------------------
# installers and conflicts
# ---------------------------------------------------------------------------


def test_install_agent_and_conflict(paths):
    spec = manager.AGENT_SPECS[manager.FLASH_ROLE]
    assert manager.install_agent(paths, spec, {}) is True
    assert manager.install_agent(paths, spec, {}) is False  # idempotent
    paths.flash_agent.write_text('name = "someone-else"\n')
    with pytest.raises(manager.ManagerError) as exc:
        manager.install_agent(paths, spec, {})
    assert exc.value.code == "conflict"


def test_install_agent_allows_previous_managed_content(paths):
    spec = manager.AGENT_SPECS[manager.FLASH_ROLE]
    manager.install_agent(paths, spec, {})
    manifest = {"hashes": {"flash_agent": manager.sha256_text_file(paths.flash_agent)}}
    paths.flash_agent.write_text("tampered\n")
    with pytest.raises(manager.ManagerError) as exc:
        manager.install_agent(paths, spec, manifest)
    assert exc.value.code == "conflict"
    paths.flash_agent.write_text(manager.agent_toml_text(spec))
    assert manager.install_agent(paths, spec, manifest) is False


def test_install_catalog_conflict_and_adopt(paths):
    payload_bytes = (json.dumps(manager.catalog_payload(), ensure_ascii=False, indent=2) + "\n").encode()
    paths.catalog.write_bytes(payload_bytes)  # identical foreign content -> adopted
    assert manager.install_catalog(paths, {}) is False
    paths.catalog.write_text('{"models": [{"slug": "foreign"}]}\n')
    with pytest.raises(manager.ManagerError) as exc:
        manager.install_catalog(paths, {})
    assert exc.value.code == "conflict"


def test_merge_hook_config_preserves_unrelated(paths):
    existing = {
        "hooks": {
            "UserPromptSubmit": [{"matcher": ".*", "hooks": [{"type": "command", "command": "echo hi"}]}]
        }
    }
    merged, adopted = manager.merge_hook_config(existing, manager.our_hook_config(paths), paths)
    assert adopted is False
    assert len(merged["hooks"]["UserPromptSubmit"]) == 1
    assert len(merged["hooks"]["SubagentStart"]) == 1


def test_merge_hook_config_adopts_equivalent(paths):
    ours = manager.our_hook_config(paths)
    merged, adopted = manager.merge_hook_config(ours, ours, paths)
    assert adopted is True
    assert merged == ours


def test_merge_hook_config_conflicts_on_foreign_matcher(paths):
    ours = manager.our_hook_config(paths)
    foreign = json.loads(json.dumps(ours))
    foreign["hooks"]["SubagentStart"][0]["hooks"][0]["command"] = "echo foreign"
    with pytest.raises(manager.ManagerError) as exc:
        manager.merge_hook_config(foreign, ours, paths)
    assert exc.value.code == "conflict"


def test_merge_hook_config_refreshes_own_entry(paths):
    ours = manager.our_hook_config(paths)
    stale = json.loads(json.dumps(ours))
    stale["hooks"]["SubagentStart"][0]["hooks"][0]["command"] = (
        "/usr/bin/python3 \"/old/path/plaintext_handoff.py\" --mode hook --state-directory \"/x\""
    )
    merged, adopted = manager.merge_hook_config(stale, ours, paths)
    assert adopted is True
    assert merged == ours


# ---------------------------------------------------------------------------
# setup / status / repair / disable / uninstall lifecycle
# ---------------------------------------------------------------------------


def test_setup_lifecycle(paths, fake_codex, no_credentials):
    before = paths.config.read_text()
    payload = manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    assert payload["status"] == "configured"
    assert payload["hook_review_required"] is True
    assert paths.flash_agent.is_file() and paths.pro_agent.is_file()
    assert paths.catalog.is_file()
    assert paths.hooks_config.is_file()
    assert (paths.runtime_skill_dir / "SKILL.md").is_file()
    assert (paths.hooks_install_dir / "plaintext_handoff.py").is_file()
    assert (paths.hooks_install_dir / "plaintext-handoff.ps1").is_file()
    assert paths.manifest.is_file()
    # Parent isolation: config.toml byte-identical after install.
    assert paths.config.read_text() == before

    manifest = manager.read_manifest(paths)
    assert manifest["original"]["parent_model"] == "gpt-5.6-test"
    assert manifest["managed"]["flash_agent"] and manifest["managed"]["pro_agent"]

    status = manager.static_status(paths, fake_codex)
    assert status["status"] == "configured"
    assert status["parent"]["model"] == "gpt-5.6-test"
    assert status["parent"]["unchanged"] is True
    assert status["agents"]["deepseek_flash"]["valid"]
    assert status["agents"]["deepseek_pro"]["valid"]
    assert status["catalog"]["registered"]
    assert status["hook"]["installed"] and status["hook"]["review_required"]
    assert status["runtime"]["codex_detected"]

    # repair is idempotent
    repaired = manager.repair(paths, fake_codex)
    assert repaired["status"] == "configured"
    assert manager.static_status(paths, fake_codex)["status"] == "configured"

    # disable removes only the hook entry
    disabled = manager.disable(paths)
    assert disabled["status"] == "disabled"
    assert paths.flash_agent.is_file() and paths.pro_agent.is_file()
    assert paths.catalog.is_file()
    hooks = json.loads(paths.hooks_config.read_text())
    assert "SubagentStart" not in hooks.get("hooks", {})
    assert manager.static_status(paths, fake_codex)["status"] == "disabled"

    # repair restores routing
    assert manager.repair(paths, fake_codex)["status"] == "configured"
    hooks = json.loads(paths.hooks_config.read_text())
    assert len(hooks["hooks"]["SubagentStart"]) == 1

    # uninstall removes everything we own, keeps the credential
    payload = manager.uninstall(paths, remove_credential=False)
    assert payload["status"] == "uninstalled"
    assert payload["credential_removed"] is False
    assert not paths.flash_agent.exists() and not paths.pro_agent.exists()
    assert not paths.catalog.exists()
    assert not paths.hooks_config.exists()
    assert not paths.runtime_skill_dir.exists()
    assert not paths.hooks_install_dir.exists()
    assert not paths.state_dir.exists()
    assert paths.config.read_text() == before


def test_setup_rolls_back_on_conflict(paths, fake_codex, no_credentials):
    before = paths.config.read_text()
    paths.agents_dir.mkdir(parents=True)
    paths.pro_agent.write_text('name = "foreign"\n')
    with pytest.raises(manager.ManagerError) as exc:
        manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    assert exc.value.code == "conflict"
    # Everything written before the failure point must be rolled back.
    assert not paths.flash_agent.exists()
    assert not paths.catalog.exists()
    assert not paths.manifest.exists()
    assert paths.config.read_text() == before


def test_setup_adopts_identical_catalog(paths, fake_codex, no_credentials):
    paths.catalog.write_bytes(
        (json.dumps(manager.catalog_payload(), ensure_ascii=False, indent=2) + "\n").encode()
    )
    payload = manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    assert payload["status"] == "configured"
    assert payload["adopted_existing"]["catalog"] is True
    assert payload["changed"]["catalog"] is False


def test_setup_conflicts_on_foreign_catalog(paths, fake_codex, no_credentials):
    paths.catalog.write_text('{"models": [{"slug": "foreign-model"}]}\n')
    with pytest.raises(manager.ManagerError) as exc:
        manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    assert exc.value.code == "conflict"
    assert paths.catalog.read_text() == '{"models": [{"slug": "foreign-model"}]}\n'


def test_setup_merges_unrelated_hooks(paths, fake_codex, no_credentials):
    paths.hooks_config.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"matcher": ".*", "hooks": [{"type": "command", "command": "echo keep-me"}]}
                    ]
                }
            }
        )
    )
    payload = manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    assert payload["status"] == "configured"
    assert payload["adopted_existing"]["hook"] is False
    hooks = json.loads(paths.hooks_config.read_text())
    assert len(hooks["hooks"]["UserPromptSubmit"]) == 1
    assert len(hooks["hooks"]["SubagentStart"]) == 1
    # disable must keep the unrelated hook
    manager.disable(paths)
    hooks = json.loads(paths.hooks_config.read_text())
    assert "UserPromptSubmit" in hooks["hooks"]
    assert "SubagentStart" not in hooks["hooks"]


def test_uninstall_restores_preexisting_catalog(paths, fake_codex, no_credentials):
    # The catalog existed before install (identical content -> adopted).
    paths.catalog.write_bytes(
        (json.dumps(manager.catalog_payload(), ensure_ascii=False, indent=2) + "\n").encode()
    )
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    assert manager.read_manifest(paths)["preexisted"]["catalog"] is True
    manager.uninstall(paths, remove_credential=False)
    # A preexisting catalog is restored, never deleted.
    assert paths.catalog.is_file()
    assert json.loads(paths.catalog.read_text()) == manager.catalog_payload()


def test_uninstall_refuses_modified_managed_file(paths, fake_codex, no_credentials):
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    paths.flash_agent.write_text('name = "tampered"\n')
    with pytest.raises(manager.ManagerError) as exc:
        manager.uninstall(paths, remove_credential=False)
    assert exc.value.code == "conflict"
    assert paths.manifest.is_file()  # still installed


def test_operation_lock_blocks_concurrent_setup(paths, fake_codex, no_credentials):
    acquired = threading.Event()
    release = threading.Event()
    outcome = {}

    def holder():
        with manager.operation_lock(paths):
            acquired.set()
            release.wait(timeout=5)

    def contender():
        try:
            with manager.operation_lock(paths, timeout_seconds=0.2):
                outcome["error"] = "lock-not-contended"
        except manager.ManagerError as exc:
            outcome["error"] = exc.code

    thread = threading.Thread(target=holder)
    thread.start()
    assert acquired.wait(timeout=5)
    contender()
    release.set()
    thread.join(timeout=5)
    assert outcome["error"] == "operation_in_progress"


def test_status_never_contains_key(paths, fake_codex, no_credentials, monkeypatch):
    secret = "sk-test-secret"
    monkeypatch.setattr(manager, "read_credential_key", lambda: secret)
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    serialized = json.dumps(manager.static_status(paths, fake_codex))
    assert secret not in serialized
    serialized = json.dumps(manager.doctor(paths, fake_codex))
    assert secret not in serialized
    assert "sk-" not in paths.manifest.read_text()


def test_status_on_empty_home(paths, fake_codex):
    status = manager.static_status(paths, fake_codex)
    assert status["status"] == "not_installed"
    assert status["installed"] is False
    assert status["parent"]["model"] == "gpt-5.6-test"
    assert status["runtime"]["codex_version"] == "codex-cli 0.148.0-test"


# ---------------------------------------------------------------------------
# live test command wiring
# ---------------------------------------------------------------------------


def _fake_smoke(paths, codex_bin, role, model):
    return {
        "role": role,
        "model": model,
        "model_provider": "deepseek",
        "agent_role": role,
        "marker_verified": True,
        "child_id": uuid.uuid4().hex,
    }


def test_run_tests_calls_both_roles_independently(paths, fake_codex, no_credentials, trusted, monkeypatch):
    calls = []
    monkeypatch.setattr(
        manager,
        "native_spawn_smoke",
        lambda paths, codex_bin, role, model: calls.append(role) or _fake_smoke(paths, codex_bin, role, model),
    )
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    payload = manager.run_tests(paths, fake_codex)
    assert payload["status"] == "ready"
    assert sorted(calls) == ["deepseek_flash", "deepseek_pro"]
    assert payload["flash"]["role"] == "deepseek_flash"
    assert payload["pro"]["role"] == "deepseek_pro"
    last_test = manager.read_manifest(paths)["last_test"]
    assert last_test["flash"]["model"] == "deepseek-v4-flash"
    assert last_test["pro"]["model"] == "deepseek-v4-pro"


def test_run_tests_requires_hook_review(paths, fake_codex, no_credentials):
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    with pytest.raises(manager.ManagerError) as exc:
        manager.run_tests(paths, fake_codex)
    assert exc.value.code == "hook_untrusted"


# ---------------------------------------------------------------------------
# CLI level
# ---------------------------------------------------------------------------


def test_cli_status_roundtrip(tmp_home, fake_codex, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_DESKTOP_BIN", fake_codex)
    exit_code = manager.main(["status", "--codex-home", str(tmp_home), "--json"])
    assert exit_code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["status"] == "not_installed"
    assert "sk-" not in out


def test_cli_setup_missing_credential(tmp_home, fake_codex, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_DESKTOP_BIN", fake_codex)
    monkeypatch.setattr(manager, "credential_present", lambda: False)
    exit_code = manager.main(["setup", "--codex-home", str(tmp_home), "--json"])
    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "credential_missing"
    assert not (tmp_home / "agents").exists()


def test_cli_setup_full_cycle(tmp_home, fake_codex, monkeypatch, capsys, no_credentials):
    monkeypatch.setenv("CODEX_DESKTOP_BIN", fake_codex)
    code = manager.main(["setup", "--codex-home", str(tmp_home), "--skip-live-test", "--json"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "configured"
    code = manager.main(["uninstall", "--codex-home", str(tmp_home), "--json"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "uninstalled"
