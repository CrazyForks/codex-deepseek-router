import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(1, str(ROOT))

from runtime.client import DeepSeekClient
from runtime.context import TaskContext, sanitize_context
from runtime.protocol import ErrorCode, RouterError
from runtime.router import choose
import codex_deepseek_router as manager


def test_plugin_manifest_and_hook_are_relative_and_parseable():
    manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "codex-deepseek-router"
    assert manifest["skills"] == "./skills/"
    assert "hooks" not in manifest
    hook = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    command = hook["hooks"]["SubagentStart"][0]["hooks"][0]["command"]
    assert "$PLUGIN_ROOT" in command
    assert "/Users/" not in command and "C:\\\\" not in command


def test_repo_marketplace_points_to_github_plugin():
    marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text())
    assert marketplace["name"] == "deepseek-router"
    entry = marketplace["plugins"][0]
    assert entry["name"] == "codex-deepseek-router"
    assert entry["source"] == {
        "source": "url",
        "url": "https://github.com/TheBlindM/codex-deepseek-router.git",
        "ref": "main",
    }
    assert (ROOT / ".codex-plugin" / "plugin.json").is_file()


def test_setup_is_plugin_first(paths, fake_codex, no_credentials):
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    assert not paths.hooks_config.exists()
    assert paths.plugin_hooks_config.is_file()
    status = manager.static_status(paths, fake_codex)
    assert status["plugin"]["manifest_valid"] is True
    assert status["hook"]["legacy"]["present"] is False


def test_legacy_migration_removes_only_owned_entry(paths, fake_codex, no_credentials):
    manager.setup(paths, fake_codex, api_key_stdin=False, skip_live_test=True)
    old = manager.our_hook_config(paths)
    old["hooks"]["UserPromptSubmit"] = [{"matcher": ".*", "hooks": [{"type": "command", "command": "echo keep"}]}]
    paths.hooks_config.write_text(json.dumps(old))
    result = manager.migrate_legacy(paths)
    assert result["status"] == "migrated"
    remaining = json.loads(paths.hooks_config.read_text())
    assert "SubagentStart" not in remaining["hooks"]
    assert "UserPromptSubmit" in remaining["hooks"]


def test_router_uses_explicit_mode_and_complexity_metadata():
    assert choose("flash", "review", {}).mode.value == "flash"
    assert choose("pro", "review", {}).mode.value == "pro"
    assert choose("auto", "architecture", {"file_count": 20, "context_size": 500000, "reasoning_depth": 9}).mode.value == "pro"


def test_context_sanitizer_withholds_attachments():
    assert "withheld" in sanitize_context({"image": "/tmp/secret.png"})
    rendered = TaskContext("inspect", {"a.py": "x"}, visual_context={"observations": ["button"]}).render("flash")
    assert "parent Codex" in rendered
    assert "original image" in rendered


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({
            "output_text": json.dumps({"summary": "ok", "findings": [], "confidence": 0.9}),
            "usage": {"input_tokens": 3, "output_tokens": 2},
        }).encode()


def test_client_returns_structured_result(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    client = DeepSeekClient("flash", opener=lambda request, timeout: FakeResponse())
    result = client.complete("inspect", context={"file_count": 1})
    assert result["status"] == "completed"
    assert result["model"] == "deepseek-v4-flash"
    assert result["confidence"] == 0.9
    assert result["usage"]["input_tokens"] == 3


def test_client_maps_missing_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("runtime.client._credential_from_manager", lambda: None)
    with pytest.raises(RouterError) as error:
        DeepSeekClient("pro", opener=lambda *args: FakeResponse()).complete("inspect")
    assert error.value.code is ErrorCode.AUTH


def test_hook_invalid_json_fails_open(tmp_path):
    script = ROOT / "hooks" / "plaintext_handoff.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--mode", "hook", "--state-directory", str(tmp_path)],
        input="not-json",
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["hookSpecificOutput"]["additionalContext"] == ""
