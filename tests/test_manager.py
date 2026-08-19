"""Manager tests: atomic write, paths, agents, catalog, transactions, lifecycle.

Lifecycle tests run against a fake codex home and never touch the real
~/.codex or the real system credential store.
"""

import json
import sqlite3
import subprocess
import sys
import threading
import uuid
from pathlib import Path

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


def test_agent_path_rejects_unknown_role(tmp_path):
    paths = manager.Paths(tmp_path)
    assert paths.agent_path("deepseek_flash") == paths.flash_agent
    assert paths.agent_path("deepseek_pro") == paths.pro_agent
    with pytest.raises(ValueError):
        paths.agent_path("some_random_role")


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
    assert "same language as the parent assignment" in flash
    assert "same language as the parent assignment" in pro


def test_macos_agent_auth_uses_the_same_python_credential_helper(monkeypatch):
    monkeypatch.setattr(manager, "platform_name", lambda: "macos")
    monkeypatch.setattr(manager, "credential_backend", lambda: "macos-keychain")

    auth = manager._provider_auth_block()

    assert f'command = {json.dumps(sys.executable)}' in auth
    assert json.dumps(str(Path(manager.__file__).resolve())) in auth
    assert '"_credential-get"' in auth
    assert "/usr/bin/security" not in auth


def test_agent_toml_never_contains_api_key_value():
    for role in manager.SUPPORTED_ROLES:
        text = manager.agent_toml_text(manager.AGENT_SPECS[role])
        assert "sk-" not in text


def test_flash_is_read_only_and_pro_owns_implementation():
    flash = manager.agent_toml_text(manager.AGENT_SPECS[manager.FLASH_ROLE])
    pro = manager.agent_toml_text(manager.AGENT_SPECS[manager.PRO_ROLE])
    assert 'sandbox_mode = "read-only"' in flash
    assert 'sandbox_mode = "workspace-write"' in pro
    assert "Never modify workspace files" in flash
    assert "read-only" in flash.split("developer_instructions")[0]
    assert "You are read-only" not in pro
    assert "For investigation:" not in pro
    assert "For implementation:" not in pro
    assert "Dynamic Policy Execution" in flash
    assert "Dynamic Policy Execution" in pro
    assert "EVIDENCE PACKET" in pro
    # The repo-shipped portable template carries the same contract.
    template = (
        Path(__file__).resolve().parents[1]
            / "agents"
        / "deepseek-flash.toml"
    ).read_text(encoding="utf-8")
    assert "Never modify workspace files" in template
    assert 'sandbox_mode = "read-only"' in template


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
    text = (tmp_home / "config.toml").read_text(encoding="utf-8")
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
    manifest = {"hashes": {"flash_agent": manager.sha256_file(paths.flash_agent)}}
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


def test_merge_hook_config_conflicts_on_router_command_with_extra_behavior(paths):
    ours = manager.our_hook_config(paths)
    foreign = json.loads(json.dumps(ours))
    foreign["hooks"]["SubagentStart"][0]["hooks"][0]["command"] += " --foreign-behavior"

    with pytest.raises(manager.ManagerError) as exc:
        manager.merge_hook_config(foreign, ours, paths)

    assert exc.value.code == "conflict"


@pytest.mark.skip(reason="Legacy Hook merging was removed from Plugin-first setup")
def test_merge_hook_config_refreshes_own_entry(paths):
    ours = manager.our_hook_config(paths)
    stale = json.loads(json.dumps(ours))
    stale["hooks"]["SubagentStart"][0]["hooks"][0]["command"] = (
        "/usr/bin/python3 \"/old/path/plaintext_handoff.py\" --mode hook --state-directory \"/x\""
    )
    merged, adopted = manager.merge_hook_config(stale, ours, paths)
    assert adopted is True
    assert merged == ours


@pytest.mark.skip(reason="Legacy Hook command ownership now requires an existing byte-identical script")
def test_hook_ownership_requires_exact_windows_command_shape(paths, monkeypatch):
    monkeypatch.setattr(manager, "platform_name", lambda: "windows")
    entry = manager.our_hook_config(paths)["hooks"]["SubagentStart"][0]
    assert manager._entry_is_ours(entry, paths) is True

    entry["hooks"][0]["commandWindows"] += " -ForeignBehavior"
    assert manager._entry_is_ours(entry, paths) is False


def test_hook_ownership_rejects_shell_metacharacters(paths):
    entry = manager.our_hook_config(paths)["hooks"]["SubagentStart"][0]
    entry["hooks"][0]["command"] = (
        'python;foreign "/old/plaintext_handoff.py" --mode hook '
        '--state-directory "/x"'
    )
    assert manager._entry_is_ours(entry, paths) is False


def _runtime_hook_metadata(paths, **overrides):
    group = manager.plugin_hook_config(paths)["hooks"]["SubagentStart"][0]
    handler = group["hooks"][0]
    hook = {
        "key": f"{paths.plugin_hooks_config}:subagent_start:0:0",
        "eventName": "subagentStart",
        "handlerType": "command",
        "matcher": group["matcher"],
        "command": (
            handler.get("commandWindows")
            if manager.platform_name() == "windows"
            else handler["command"]
        ),
        "sourcePath": str(paths.plugin_hooks_config),
        "enabled": True,
        "currentHash": "sha256:test",
        "trustStatus": "trusted",
    }
    hook.update(overrides)
    return hook


def test_hook_trusted_uses_codex_runtime_metadata(paths, fake_codex, monkeypatch):
    """Trust is keyed by hooks.json + current hash, not by command text in config.toml."""
    assert str(paths.hooks_install_dir / "plaintext_handoff.py") not in paths.config.read_text(
        encoding="utf-8"
    )
    monkeypatch.setattr(
        manager,
        "_query_codex_hooks",
        lambda paths, codex_bin: {
            "hooks": [_runtime_hook_metadata(paths)],
            "warnings": [],
            "errors": [],
        },
        raising=False,
    )

    assert manager.hook_trusted(paths, fake_codex) is True


@pytest.mark.parametrize("trust_status", ["untrusted", "modified", None])
def test_hook_trusted_fails_closed_for_unapproved_hash(
    paths, fake_codex, monkeypatch, trust_status
):
    monkeypatch.setattr(
        manager,
        "_query_codex_hooks",
        lambda paths, codex_bin: {
            "hooks": [_runtime_hook_metadata(paths, trustStatus=trust_status)],
            "warnings": [],
            "errors": [],
        },
        raising=False,
    )

    assert manager.hook_trusted(paths, fake_codex) is False


def test_hook_trusted_rejects_different_command(paths, fake_codex, monkeypatch):
    monkeypatch.setattr(
        manager,
        "_query_codex_hooks",
        lambda paths, codex_bin: {
            "hooks": [_runtime_hook_metadata(paths, command="python3 /tmp/foreign.py")],
            "warnings": [],
            "errors": [],
        },
        raising=False,
    )

    assert manager.hook_trusted(paths, fake_codex) is False


# ---------------------------------------------------------------------------
# setup / status / repair / disable / uninstall lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="Plugin-first setup no longer writes global hooks or duplicate Skills")
def test_setup_lifecycle(paths, fake_codex, no_credentials):
    before = paths.config.read_text(encoding="utf-8")
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
    assert paths.config.read_text(encoding="utf-8") == before

    manifest = manager.read_manifest(paths)
    assert manifest["original"]["parent_model"] == "gpt-5.6-test"
    assert manifest["managed"]["flash_agent"] and manifest["managed"]["pro_agent"]
    assert set(manifest["hashes"]) == set(manager.managed_assets(paths))

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
    hooks = json.loads(paths.hooks_config.read_text(encoding="utf-8"))
    assert "SubagentStart" not in hooks.get("hooks", {})
    assert manager.static_status(paths, fake_codex)["status"] == "disabled"

    # repair restores routing
    assert manager.repair(paths, fake_codex)["status"] == "configured"
    hooks = json.loads(paths.hooks_config.read_text(encoding="utf-8"))
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
    assert paths.config.read_text(encoding="utf-8") == before


@pytest.mark.skip(reason="Plugin-first setup does not rewrite global hooks.json")
def test_setup_reads_utf8_when_platform_default_is_cp1252(
    paths, fake_codex, no_credentials, monkeypatch
):
    """Reproduce Windows runners where Path.read_text defaults to CP1252."""
    original_read_text = Path.read_text

    def cp1252_default(path, encoding=None, errors=None):
        return original_read_text(
            path,
            encoding=encoding or "cp1252",
            errors=errors,
        )

    monkeypatch.setattr(Path, "read_text", cp1252_default)

    payload = manager.setup(
        paths,
        fake_codex,
        api_key_stdin=False,
        skip_live_test=True,
    )

    assert payload["status"] == "configured"
    hooks = json.loads(paths.hooks_config.read_text(encoding="utf-8"))
    assert "正在传递" in hooks["hooks"]["SubagentStart"][0]["hooks"][0]["statusMessage"]


def test_setup_rolls_back_on_conflict(paths, fake_codex, no_credentials):
    before = paths.config.read_text(encoding="utf-8")
    paths.agents_dir.mkdir(parents=True)
    paths.pro_agent.write_text('name = "foreign"\n')
    with pytest.raises(manager.ManagerError) as exc:
        manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    assert exc.value.code == "conflict"
    # Everything written before the failure point must be rolled back.
    assert not paths.flash_agent.exists()
    assert not paths.catalog.exists()
    assert not paths.manifest.exists()
    assert paths.config.read_text(encoding="utf-8") == before


@pytest.mark.skip(reason="Plugin-owned hook scripts are not copied into global Codex state")
def test_setup_conflicts_on_foreign_hook_script(paths, fake_codex, no_credentials):
    paths.hooks_install_dir.mkdir(parents=True)
    (paths.hooks_install_dir / "plaintext_handoff.py").write_text("# foreign script\n")
    with pytest.raises(manager.ManagerError) as exc:
        manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    assert exc.value.code == "conflict"
    # The foreign script survives untouched; everything else was rolled back.
    assert (
        paths.hooks_install_dir / "plaintext_handoff.py"
    ).read_text(encoding="utf-8") == "# foreign script\n"
    assert not (paths.hooks_install_dir / "plaintext-handoff.ps1").exists()
    assert not paths.flash_agent.exists()
    assert not paths.catalog.exists()
    assert not paths.manifest.exists()


@pytest.mark.skip(reason="Plugin-first setup has no global Hook/Skill transaction")
def test_setup_rollback_covers_hook_scripts_and_skill(paths, fake_codex, no_credentials):
    ours = manager.our_hook_config(paths)
    foreign = json.loads(json.dumps(ours))
    foreign["hooks"]["SubagentStart"][0]["hooks"][0]["command"] = "echo foreign-hook"
    paths.hooks_config.write_text(json.dumps(foreign))
    with pytest.raises(manager.ManagerError) as exc:
        manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    assert exc.value.code == "conflict"
    # Assets written before the hooks.json merge conflict must be rolled back too.
    assert not (paths.hooks_install_dir / "plaintext_handoff.py").exists()
    assert not (paths.hooks_install_dir / "plaintext-handoff.ps1").exists()
    assert not (paths.runtime_skill_dir / "SKILL.md").exists()
    assert not paths.flash_agent.exists()
    assert not paths.catalog.exists()
    # The foreign hooks.json is byte-identical after rollback.
    assert json.loads(paths.hooks_config.read_text(encoding="utf-8")) == foreign


@pytest.mark.skip(reason="Plugin-first setup does not rewrite global hooks.json")
def test_setup_rollback_preserves_existing_file_mode(paths, fake_codex, no_credentials):
    ours = manager.our_hook_config(paths)
    foreign = json.loads(json.dumps(ours))
    foreign["hooks"]["SubagentStart"][0]["hooks"][0]["command"] = "echo foreign-hook"
    paths.hooks_config.write_text(json.dumps(foreign))
    paths.hooks_config.chmod(0o644)
    original_mode = paths.hooks_config.stat().st_mode

    with pytest.raises(manager.ManagerError):
        manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)

    assert paths.hooks_config.stat().st_mode == original_mode


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
    assert paths.catalog.read_text(encoding="utf-8") == '{"models": [{"slug": "foreign-model"}]}\n'


@pytest.mark.skip(reason="Plugin Hook is shipped by the Plugin and never merged globally")
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
    hooks = json.loads(paths.hooks_config.read_text(encoding="utf-8"))
    assert len(hooks["hooks"]["UserPromptSubmit"]) == 1
    assert len(hooks["hooks"]["SubagentStart"]) == 1
    manager.repair(paths, fake_codex)
    hooks = json.loads(paths.hooks_config.read_text(encoding="utf-8"))
    assert len(hooks["hooks"]["UserPromptSubmit"]) == 1
    assert len(hooks["hooks"]["SubagentStart"]) == 1
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    hooks = json.loads(paths.hooks_config.read_text(encoding="utf-8"))
    assert len(hooks["hooks"]["UserPromptSubmit"]) == 1
    assert len(hooks["hooks"]["SubagentStart"]) == 1
    # disable must keep the unrelated hook
    manager.disable(paths)
    hooks = json.loads(paths.hooks_config.read_text(encoding="utf-8"))
    assert "UserPromptSubmit" in hooks["hooks"]
    assert "SubagentStart" not in hooks["hooks"]


@pytest.mark.skip(reason="Plugin Hook status is validated from the Plugin manifest")
def test_status_requires_full_hook_invariant(paths, fake_codex, no_credentials):
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    assert manager.static_status(paths, fake_codex)["status"] == "configured"

    # Externally remove the router entry but keep the file (review finding P2-4).
    config = json.loads(paths.hooks_config.read_text(encoding="utf-8"))
    config["hooks"].pop("SubagentStart", None)
    paths.hooks_config.write_text(json.dumps(config))
    status = manager.static_status(paths, fake_codex)
    assert status["status"] == "partial"
    assert status["hook"]["entry_present"] is False

    # Repair restores the entry.
    manager.repair(paths, fake_codex)
    assert manager.static_status(paths, fake_codex)["status"] == "configured"

    # Missing installed scripts must also degrade the status.
    (paths.hooks_install_dir / "plaintext_handoff.py").unlink()
    status = manager.static_status(paths, fake_codex)
    assert status["status"] == "partial"
    assert status["hook"]["files_installed"] is False


@pytest.mark.parametrize(
    "asset",
    ("python_hook", "powershell_hook", "runtime_skill"),
)
@pytest.mark.skip(reason="Plugin-owned Skill/Hook assets are outside manager state")
def test_status_rejects_modified_runtime_assets(paths, fake_codex, no_credentials, asset):
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    targets = {
        "python_hook": paths.hooks_install_dir / "plaintext_handoff.py",
        "powershell_hook": paths.hooks_install_dir / "plaintext-handoff.ps1",
        "runtime_skill": paths.runtime_skill_dir / "SKILL.md",
    }
    targets[asset].write_text("foreign or broken content\n")

    status = manager.static_status(paths, fake_codex)

    assert status["status"] == "partial"
    assert status["hook"]["files_installed"] is False


@pytest.mark.skip(reason="Plugin-owned Hook assets are outside manager state")
def test_status_uses_byte_exact_runtime_asset_hashes(paths, fake_codex, no_credentials):
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    target = paths.hooks_install_dir / "plaintext_handoff.py"
    target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))

    status = manager.static_status(paths, fake_codex)

    assert status["status"] == "partial"
    assert status["hook"]["files_installed"] is False


def test_unknown_hash_version_never_uses_legacy_normalization(tmp_path):
    target = tmp_path / "asset.txt"
    target.write_bytes(b"a\r\nb\r\n")
    legacy_hash = manager.sha256_text_file(target)
    manifest = {"hashes": {"asset": legacy_hash}}
    assert manager._file_is_ours(target, manifest, "asset") is True

    manifest["hash_version"] = 999
    assert manager._file_is_ours(target, manifest, "asset") is False


@pytest.mark.skip(reason="Plugin Hook is not a global hooks.json entry")
def test_status_requires_exact_router_hook_entry(paths, fake_codex, no_credentials):
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    config = json.loads(paths.hooks_config.read_text(encoding="utf-8"))
    entry = config["hooks"]["SubagentStart"][0]["hooks"][0]
    entry["command"] += " --foreign-behavior"
    paths.hooks_config.write_text(json.dumps(config))

    status = manager.static_status(paths, fake_codex)

    assert status["status"] == "partial"
    assert status["hook"]["entry_present"] is False


@pytest.mark.skip(reason="Plugin Hook is not a global hooks.json entry")
def test_status_rejects_conflicting_duplicate_router_hook(paths, fake_codex, no_credentials):
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    config = json.loads(paths.hooks_config.read_text(encoding="utf-8"))
    foreign = json.loads(json.dumps(config["hooks"]["SubagentStart"][0]))
    foreign["hooks"][0]["command"] += " --foreign-behavior"
    config["hooks"]["SubagentStart"].append(foreign)
    paths.hooks_config.write_text(json.dumps(config))

    status = manager.static_status(paths, fake_codex)

    assert status["status"] == "partial"
    assert status["hook"]["entry_present"] is False


def test_status_ready_after_live_test_and_downgrades_when_modified(
    paths, fake_codex, no_credentials, trusted, monkeypatch
):
    monkeypatch.setattr(manager, "native_spawn_smoke", _fake_smoke)
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    assert manager.static_status(paths, fake_codex)["status"] == "configured"

    manager.run_tests(paths, fake_codex)
    status = manager.static_status(paths, fake_codex)
    assert status["status"] == "ready"
    assert status["last_test"]["flash"]["role"] == "deepseek_flash"

    # A second test run on a ready install is allowed.
    assert manager.run_tests(paths, fake_codex)["status"] == "ready"

    # Tampering with a tested asset invalidates the evidence.
    paths.flash_agent.write_text('name = "modified"\n')
    assert manager.static_status(paths, fake_codex)["status"] == "partial"


def test_repair_requires_fresh_live_tests(paths, fake_codex, no_credentials, trusted, monkeypatch):
    monkeypatch.setattr(manager, "native_spawn_smoke", _fake_smoke)
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    manager.run_tests(paths, fake_codex)
    assert manager.static_status(paths, fake_codex)["status"] == "ready"

    manager.repair(paths, fake_codex)

    status = manager.static_status(paths, fake_codex)
    assert status["status"] == "configured"
    assert status["last_test"] is None


def test_repair_refreshes_previous_managed_handoff_runtime(
    paths, fake_codex, no_credentials
):
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    target = paths.hooks_install_dir / "plaintext_handoff.py"
    target.write_text("# previous router-managed version\n")
    manifest = manager.read_manifest(paths)
    manifest["hashes"]["handoff_script_py"] = manager.sha256_file(target)
    manager.write_manifest(paths, manifest)

    repaired = manager.repair(paths, fake_codex)

    assert repaired["changed"]["handoff_runtime"] is True
    assert target.read_bytes() == (
        Path(manager.__file__).resolve().parents[1] / "hooks" / "plaintext_handoff.py"
    ).read_bytes()


def test_installed_staging_helper_includes_reasoning_runtime(paths):
    manager.install_hook_files(paths, {})

    script = paths.hooks_install_dir / "plaintext_handoff.py"
    reasoning = paths.hooks_install_dir / "runtime" / "reasoning.py"
    assert script.is_file()
    assert reasoning.is_file()

    process = subprocess.run(
        [
            sys.executable,
            str(script),
            "--mode",
            "stage",
            "--agent-type",
            "deepseek_pro",
            "--policy",
            "REACT",
            "--modality",
            "TEXT_ONLY",
            "--state-directory",
            str(paths.handoff_dir),
        ],
        input="bounded staging probe",
        text=True,
        capture_output=True,
    )

    assert process.returncode == 0, process.stderr
    payload = json.loads(process.stdout)
    assert payload["staged"] is True
    assert Path(payload["pending_path"]).is_file()


@pytest.mark.skip(reason="Legacy Hook assets are migrated explicitly, not repaired")
def test_repair_migrates_legacy_normalized_asset_hashes(paths, fake_codex, no_credentials):
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    manifest = manager.read_manifest(paths)
    manifest.pop("hash_version")
    manifest["hashes"] = {
        key: manager.sha256_text_file(asset.path)
        for key, asset in manager.managed_assets(paths).items()
    }
    manager.write_manifest(paths, manifest)
    powershell = paths.hooks_install_dir / "plaintext-handoff.ps1"
    normalized = powershell.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    powershell.write_bytes(normalized.replace(b"\n", b"\r\n"))

    result = manager.repair(paths, fake_codex)

    assert result["status"] == "configured"
    migrated = manager.read_manifest(paths)
    assert migrated["hash_version"] == manager.HASH_VERSION_EXACT_BYTES
    assert migrated["hashes"]["hook_script_ps1"] == manager.sha256_file(powershell)


def test_macos_store_credential_uses_keychain_constants_and_updates_in_place(monkeypatch):
    """Keychain writes are argv-free, API-valid, and preserve an existing item."""
    import ctypes

    calls = []
    created = []

    def allocate():
        ref = 101 + len(created)
        created.append(ref)
        return ref

    class FakeCF:
        def CFStringCreateWithCString(self, alloc, value, encoding):
            calls.append(("string", bytes(value).decode()))
            return allocate()

        def CFDataCreate(self, alloc, buffer, length):
            calls.append(("data", bytes(buffer)[:length]))
            return allocate()

        def CFDictionaryCreate(self, alloc, keys, values, count, kcb, vcb):
            calls.append(("dictionary", tuple(keys[:count]), tuple(values[:count]), kcb, vcb))
            return allocate()

        def CFRelease(self, ref):
            calls.append(("release", ref))

    class FakeSecurity:
        def __init__(self):
            self.adds = 0

        def SecItemAdd(self, item, out):
            self.adds += 1
            calls.append(("SecItemAdd", self.adds))
            return -25299

        def SecItemUpdate(self, query, attributes):
            calls.append(("SecItemUpdate", query, attributes))
            return 0

    monkeypatch.setattr(manager, "_macos_security_framework", lambda: (FakeSecurity(), FakeCF(), ctypes))
    monkeypatch.setattr(
        manager,
        "_macos_security_constants",
        lambda security, cf, ctypes_module: {
            "class": 11,
            "generic_password": 12,
            "service": 13,
            "account": 14,
            "value_data": 15,
            "key_callbacks": 16,
            "value_callbacks": 17,
        },
        raising=False,
    )

    def _no_subprocess(*args, **kwargs):
        raise AssertionError("credential write must not spawn subprocesses (argv leak)")

    monkeypatch.setattr(manager.subprocess, "run", _no_subprocess)

    manager._macos_store_credential("sk-test-secret")
    assert ("data", b"sk-test-secret") in calls
    assert ("string", "sk-test-secret") not in calls
    assert [entry for entry in calls if entry[0] == "string"] == [
        ("string", manager.CREDENTIAL_TARGET),
        ("string", manager.credential_account()),
    ]
    assert [entry for entry in calls if entry[0] == "SecItemAdd"] == [("SecItemAdd", 1)]
    assert len([entry for entry in calls if entry[0] == "SecItemUpdate"]) == 1
    dictionaries = [entry for entry in calls if entry[0] == "dictionary"]
    assert dictionaries
    assert all(entry[3:] == (16, 17) for entry in dictionaries)
    assert sorted(entry[1] for entry in calls if entry[0] == "release") == sorted(created)


def test_macos_store_credential_releases_partial_allocations(monkeypatch):
    import ctypes

    released = []

    class FailingCF:
        def __init__(self):
            self.strings = 0

        def CFStringCreateWithCString(self, alloc, value, encoding):
            self.strings += 1
            return 101 if self.strings == 1 else None

        def CFDataCreate(self, alloc, buffer, length):
            raise AssertionError("allocation must stop after a null CFString")

        def CFDictionaryCreate(self, alloc, keys, values, count, kcb, vcb):
            raise AssertionError("native dictionaries must not receive null values")

        def CFRelease(self, ref):
            released.append(ref)

    fake_cf = FailingCF()
    monkeypatch.setattr(manager, "_macos_security_framework", lambda: (object(), fake_cf, ctypes))
    monkeypatch.setattr(
        manager,
        "_macos_security_constants",
        lambda security, cf, ctypes_module: {
            "class": 11,
            "generic_password": 12,
            "service": 13,
            "account": 14,
            "value_data": 15,
            "key_callbacks": 16,
            "value_callbacks": 17,
        },
    )

    with pytest.raises(manager.ManagerError) as exc:
        manager._macos_store_credential("sk-test-secret")

    assert exc.value.code == "credential_write_failed"
    assert released == [101]


def test_macos_read_credential_uses_security_framework_without_subprocess(monkeypatch):
    import ctypes

    secret = b"sk-test-secret"
    secret_buffer = ctypes.create_string_buffer(secret)
    released = []
    created = []

    def allocate():
        ref = 101 + len(created)
        created.append(ref)
        return ref

    class FakeCF:
        def CFStringCreateWithCString(self, alloc, value, encoding):
            return allocate()

        def CFDictionaryCreate(self, alloc, keys, values, count, kcb, vcb):
            return allocate()

        def CFDataGetLength(self, ref):
            assert ref == 999
            return len(secret)

        def CFDataGetBytePtr(self, ref):
            assert ref == 999
            return ctypes.addressof(secret_buffer)

        def CFRelease(self, ref):
            released.append(ref)

    class FakeSecurity:
        def SecItemCopyMatching(self, query, result):
            ctypes.cast(result, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(999)
            return 0

    monkeypatch.setattr(manager, "_macos_security_framework", lambda: (FakeSecurity(), FakeCF(), ctypes))
    monkeypatch.setattr(
        manager,
        "_macos_security_constants",
        lambda security, cf, ctypes_module: {
            "class": 11,
            "generic_password": 12,
            "service": 13,
            "account": 14,
            "return_data": 15,
            "true": 16,
            "key_callbacks": 17,
            "value_callbacks": 18,
        },
    )

    def fail_if_spawned(*args, **kwargs):
        raise AssertionError("credential reads must not spawn /usr/bin/security")

    monkeypatch.setattr(manager.subprocess, "run", fail_if_spawned)

    assert manager._macos_read_credential() == secret.decode()
    assert sorted(released) == sorted(created + [999])


def test_macos_credential_presence_does_not_decrypt_the_secret(monkeypatch):
    monkeypatch.delenv(manager.API_KEY_ENV, raising=False)
    monkeypatch.setattr(manager, "platform_name", lambda: "macos")
    monkeypatch.setattr(manager, "credential_backend", lambda: "macos-keychain")
    monkeypatch.setattr(manager, "_macos_credential_exists", lambda: True, raising=False)

    def fail_if_read():
        pytest.fail("credential presence checks must not decrypt the API key")

    monkeypatch.setattr(manager, "_macos_read_credential", fail_if_read)

    assert manager.credential_present() is True


def test_static_status_checks_credential_presence_once(paths, fake_codex, no_credentials, monkeypatch):
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    calls = 0

    def credential_present():
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(manager, "credential_present", credential_present)

    status = manager.static_status(paths, fake_codex)

    assert status["status"] == "configured"
    assert calls == 1


@pytest.mark.skip(reason="requires a writable native Keychain test item")
def test_macos_keychain_dictionary_is_accepted_by_native_api():
    security, cf, ctypes = manager._macos_security_framework()
    constants = manager._macos_security_constants(security, cf, ctypes)
    security.SecItemCopyMatching.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    security.SecItemCopyMatching.restype = ctypes.c_int32

    def cf_string(value):
        return cf.CFStringCreateWithCString(
            None,
            value.encode(),
            manager._K_CF_STRING_ENCODING_UTF8,
        )

    target = cf_string(f"codex-router-readonly-test-{uuid.uuid4().hex}")
    account = cf_string("nobody")
    entries = [
        (constants["class"], constants["generic_password"]),
        (constants["service"], target),
        (constants["account"], account),
    ]
    keys = (ctypes.c_void_p * len(entries))(*[key for key, _ in entries])
    values = (ctypes.c_void_p * len(entries))(*[value for _, value in entries])
    query = cf.CFDictionaryCreate(
        None,
        keys,
        values,
        len(entries),
        constants["key_callbacks"],
        constants["value_callbacks"],
    )
    try:
        result = ctypes.c_void_p()
        status = security.SecItemCopyMatching(query, ctypes.byref(result))
        assert status == -25300  # errSecItemNotFound proves the query was valid.
    finally:
        cf.CFRelease(query)
        cf.CFRelease(target)
        cf.CFRelease(account)


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
    assert json.loads(paths.catalog.read_text(encoding="utf-8")) == manager.catalog_payload()


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


def test_windows_operation_lock_read_denial_means_contended(monkeypatch):
    class LockedByte:
        def seek(self, offset):
            pass

        def read(self, size):
            raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(manager, "fcntl", None)
    monkeypatch.setattr(manager, "msvcrt", object())

    assert manager.try_acquire_file_lock(LockedByte()) is False


def test_status_never_contains_key(paths, fake_codex, no_credentials, monkeypatch):
    secret = "sk-test-secret"
    monkeypatch.setattr(manager, "read_credential_key", lambda: secret)
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    serialized = json.dumps(manager.static_status(paths, fake_codex))
    assert secret not in serialized
    serialized = json.dumps(manager.doctor(paths, fake_codex))
    assert secret not in serialized
    assert "sk-" not in paths.manifest.read_text(encoding="utf-8")


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


def test_parse_smoke_events_uses_final_agent_message_for_v2_wait():
    child_id = "01a00fc4-1038-70e1-9224-7c779e6afe5c"
    expected = "NATIVE_DEEPSEEK_FLASH_OK marker 391"
    stdout = "\n".join(
        json.dumps(event)
        for event in (
            {"type": "thread.started", "thread_id": "parent-thread"},
            {
                "type": "item.completed",
                "item": {
                    "type": "collab_tool_call",
                    "tool": "spawn_agent",
                    "receiver_thread_ids": [child_id],
                    "agents_states": {},
                },
            },
            # Multi-agent V2 wait events carry no receiver/message payload. The
            # parent is explicitly instructed to echo the child's final answer.
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": expected},
            },
        )
    )

    evidence = manager._parse_native_smoke_events(stdout)

    assert evidence["parent_thread_id"] == "parent-thread"
    assert evidence["child_ids"] == [child_id]
    assert evidence["final_message"] == expected


def test_recent_child_ids_recovers_v2_child_when_exec_omits_collab_item(paths):
    state_db = paths.codex_home / "state_test.sqlite"
    with sqlite3.connect(state_db) as connection:
        connection.execute(
            """CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                created_at_ms INTEGER,
                model_provider TEXT,
                model TEXT,
                agent_role TEXT
            )"""
        )
        connection.executemany(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
            (
                ("too-old", 999, "deepseek", "deepseek-v4-flash", "deepseek_flash"),
                ("wrong-role", 2001, "deepseek", "deepseek-v4-pro", "deepseek_pro"),
                ("expected-child", 2002, "deepseek", "deepseek-v4-flash", "deepseek_flash"),
            ),
        )

    assert manager._recent_child_ids(
        paths,
        role="deepseek_flash",
        model="deepseek-v4-flash",
        started_after_ms=2000,
        parent_thread_id="parent-thread",
    ) == ["expected-child"]


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


def test_cli_credential_helper_prints_only_the_key(monkeypatch, capsys):
    monkeypatch.setattr(manager, "read_credential_key", lambda: "sk-test-secret")

    exit_code = manager.main(["_credential-get"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "sk-test-secret"
    assert captured.err == ""


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
