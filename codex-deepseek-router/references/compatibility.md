# Compatibility

## Supported platforms

| Platform | Credential backend | Agent TOML auth | Verified live smoke |
|---|---|---|---|
| macOS | Keychain (Security.framework) | same-Python Keychain helper (generated at install) | Codex CLI `0.148.0-alpha.9` (this machine), oil-oil baseline |
| Windows | Credential Manager | `env_key = "DEEPSEEK_API_KEY"` | Utopia-V baseline: Windows Desktop `26.727.6591.0` |
| Linux | `DEEPSEEK_API_KEY` env var | `env_key` | protocol/static tests; live smoke recommended |

- Python 3.9+ (stdlib only; no third-party runtime dependencies).
- Codex Desktop or CLI at least started once; the manager uses the desktop
  bundled runtime when discoverable (`CODEX_DESKTOP_BIN` overrides).

## Configuration layout

Default `CODEX_HOME` is `~/.codex`:

- Agent files: `$CODEX_HOME/agents/deepseek-flash.toml`,
  `$CODEX_HOME/agents/deepseek-pro.toml` — the DeepSeek provider lives
  **inside** these files. The top-level `config.toml` is never modified by
  the installer; the parent model/provider/login stay untouched.
- Dual-model registry: `$CODEX_HOME/models.json` (both
  `deepseek-v4-flash` and `deepseek-v4-pro`; never one without the other).
  The `model_catalog_json` pointer in `config.toml` is **not** changed.
- Hook config: `$CODEX_HOME/hooks.json` (merged; unrelated hooks preserved).
  Hook scripts: `$CODEX_HOME/hooks/codex-deepseek-router/`.
- Runtime routing skill: `$CODEX_HOME/skills/use-deepseek-router/`.
- Manager state, backups, manifest and handoff state:
  `$CODEX_HOME/deepseek-router/`.
- Credential target: `io.github.codex-deepseek-router.deepseek-api-key`
  (macOS Keychain / Windows Credential Manager).

## Tested baselines

| Component | Evidence |
|---|---|
| Codex CLI | `0.148.0-alpha.9` (macOS desktop bundle, this machine) |
| Windows Codex Desktop | `26.727.6591.0` (Utopia-V live baseline) |
| DeepSeek models | `deepseek-v4-flash`, `deepseek-v4-pro`, Responses API |
| Transport | V2 + `fork_turns="none"` + `SubagentStart` plaintext hook |
| Parent isolation | installer never writes config.toml (stronger than oil-oil's V1 workaround, which we deliberately do not copy) |

Version numbers are diagnostic only. Compatibility is proven by the live
smoke tests (`test` command): native `spawn_agent` → DeepSeek child →
callback, plus `state_*.sqlite` thread metadata
(`model_provider=deepseek`, correct `model`, correct `agent_role`) and a
random challenge marker. Flash passing never implies Pro passing.

## Known boundaries

- **Windows Desktop + Credential Manager command auth**: a command-backed
  HKCU lookup fails under the Desktop sandbox identity
  (Utopia-V issue #6). Therefore Windows agents use `env_key`; the manager
  stores the key in Credential Manager and injects it into `codex exec`
  smoke runs. For Desktop use, set `DEEPSEEK_API_KEY` as a user environment
  variable and fully restart the Codex app.
- **Hook trust**: Codex requires the user to review the hook with `/hooks`.
  The installer never writes or forges the trust record; after review Codex
  itself records it in `config.toml`, which the manager detects
  heuristically (presence of the installed script path).
- **Inline hooks**: a `[hooks]` table inside `config.toml` is not merged by
  this installer; migrate it to `hooks.json` or review manually.
- **macOS Python identity**: setup records the exact Python executable in the
  generated agent auth block. Run `repair` after changing Python installations
  so Keychain reads continue under the identity that owns the item.
