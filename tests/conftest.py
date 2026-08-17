"""Shared test fixtures for the codex-deepseek-router test suite."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "codex-deepseek-router"
sys.path.insert(0, str(PACKAGE / "scripts"))
sys.path.insert(0, str(PACKAGE / "hooks"))

import codex_deepseek_router as manager  # noqa: E402
import plaintext_handoff as handoff  # noqa: E402

PARENT_MODEL = "gpt-5.6-test"
PARENT_PROVIDER = "openai"


@pytest.fixture
def tmp_home(tmp_path: Path) -> Path:
    """A fake codex home with a minimal parent config.toml."""
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'model = "{PARENT_MODEL}"\nmodel_provider = "{PARENT_PROVIDER}"\n'
    )
    return home


@pytest.fixture
def paths(tmp_home: Path) -> manager.Paths:
    return manager.Paths(tmp_home)


@pytest.fixture
def fake_codex(tmp_path: Path, monkeypatch) -> str:
    """A stub Codex runtime path. Unit tests never execute it; the version
    probe is stubbed so the fixture works on every platform (the real
    smoke test command is the only place that spawns the runtime)."""
    binary = tmp_path / "fake-codex"
    binary.write_text("not executed by unit tests")
    monkeypatch.setattr(manager, "codex_version_text", lambda codex_bin: "codex-cli 0.148.0-test")
    return str(binary)


@pytest.fixture
def no_credentials(monkeypatch):
    """Pretend a credential is always present so setup can proceed without
    touching the real system credential store."""
    monkeypatch.setattr(manager, "credential_present", lambda: True)


@pytest.fixture
def handoff_dir(tmp_path: Path) -> Path:
    return tmp_path / "handoff-state"


@pytest.fixture
def trusted(monkeypatch):
    """Pretend the user reviewed the hook with /hooks."""
    monkeypatch.setattr(manager, "hook_trusted", lambda paths: True)
