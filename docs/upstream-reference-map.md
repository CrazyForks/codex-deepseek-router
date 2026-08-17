# Upstream Reference Map

> First-time source extraction only. Subsequent coding agents read this map
> instead of re-consuming the upstream repositories, unless an implementation
> actually fails (rule AE of the project plan).

Source repositories:

- `oil-oil/codex-deepseek-subagent` (MIT) — manager skeleton
- `Utopia-V/codex-deepseek-subagent` (MIT) — plaintext handoff transport
- `yjh051108/dsh-routing-suite` — reasoning-policy inspiration only

Format: feature → source repo → source file → source symbol →
target file in this project → adaptation required.

## Manager

| Feature | Source repo | Source file | Source symbol | Target file | Adaptation |
|---|---|---|---|---|---|
| CLI structure | oil-oil | `codex-deepseek-subagent/scripts/codex_deepseek.py` | `main`, `build_parser`, `emit`, `result` | `scripts/codex_deepseek_router.py` | dual-agent, no `--model` switching |
| atomic write | oil-oil | same | `atomic_write` | same target | unchanged |
| backup / rollback | oil-oil | same | `make_backup`, `restore_backup` | same target | tracked set covers 2 agents, catalog and explicit staging helpers; Plugin files are not managed by setup |
| process lock | oil-oil | same | `operation_lock`, `try_acquire_file_lock` | same target | unchanged (fcntl / msvcrt) |
| manifest | oil-oil | same | `write_manifest`, `read_manifest` | same target | no `selected_model`, no forced V1 |
| macOS Keychain | oil-oil | same | `_macos_read_credential` etc. | same target | read/write/delete via Security.framework in the same Python identity; status uses a metadata-only query; secret never enters argv; target renamed to `io.github.codex-deepseek-router.deepseek-api-key` |
| Windows Credential Manager | oil-oil | same | `_windows_credential_api` etc. | same target | target renamed (see above) |
| runtime discovery | oil-oil | same | `find_desktop_codex`, `codex_version_text` | same target | unchanged |
| static status | oil-oil | same | `static_status` | same target | restructured for two agents + hook trust |
| native smoke | oil-oil | same | `native_test`, `query_child_metadata`, `wait_for_child_metadata` | same target | two roles, marker per role, staged-assignment prompt (hook path, not forced V1) |
| conflict detection | oil-oil | same | `provider_conflicts`, `compatible_existing`, `conflict` errors | same target | rewritten for foreign agent/catalog/hook files |
| adopted existing | oil-oil | same | `install` adoption flags | same target | kept as `adopted_existing` manifest fields |
| V1 force-disable | oil-oil | same | `PARENT_MULTI_AGENT_VERSION = "v1"` | — | **prohibited, not migrated** |
| single `DeepSeek.toml` agent | oil-oil | same | `ROLE`, `paths.agent` | — | **prohibited, replaced by `deepseek-flash.toml` + `deepseek-pro.toml`** |
| Flash/Pro repair switching | oil-oil | same | `--model`, `resolve_selected_model` | — | **prohibited, both models always installed** |

## Handoff transport

| Feature | Source repo | Source file | Source symbol | Target file | Adaptation |
|---|---|---|---|---|---|
| envelope schema / validation | Utopia-V | `hooks/plaintext_handoff.py` | `validate_envelope`, `parse_timestamp` | `hooks/plaintext_handoff.py` | dual-role + `policy`/`modality`/`visual_context`/`evidence_packet` fields |
| staging | Utopia-V | same | `stage_locked`, `stage` | same target | per-role pending files; `os.link` no-clobber + `os.replace` for expired |
| claim / consume | Utopia-V | same | `run_target_hook_locked`, `quarantine_claim` | same target | claim-first-then-read kept verbatim; per-role claimed/failed names |
| TTL reconcile | Utopia-V | same | `reconcile_claims` | same target (`reconcile`) | per-role |
| POSIX lock | Utopia-V | same | `state_lock` | same target | per-role lock files + msvcrt fallback for the Python path |
| child context builder | Utopia-V | same | inline in `run_target_hook_locked` | same target (`build_child_context`) | baseline section O structure |
| Hook output shape | Utopia-V | same | `hookSpecificOutput.additionalContext` | same target | unchanged |
| PowerShell variant | Utopia-V | `hooks/plaintext-handoff.ps1` | full protocol | `hooks/plaintext-handoff.ps1` | `-AgentType` parameter, policy/modality/packet fields, per-role files |
| Hook JSON templates | Utopia-V | `hooks/hooks.posix.example.json`, `hooks/hooks.windows.example.json` | — | `hooks/hooks.json` | Plugin-owned, `PLUGIN_ROOT`-relative command and combined matcher `^(deepseek_flash|deepseek_pro)$` |
| Flash-only role hardcode | Utopia-V | both scripts | `AGENT_TYPE = "v4_flash_worker"` | — | **parameterized away** |
| read-only default for Pro | Utopia-V | `agents/v4-flash-worker.toml` | `sandbox_mode = "read-only"` | — | **not applied to Pro**: `workspace-write` |

## Agent/provider pattern

| Feature | Source repo | Source file | Source symbol | Target file | Adaptation |
|---|---|---|---|---|---|
| self-contained provider in agent TOML | Utopia-V | `agents/v4-flash-worker.toml` | `[model_providers.deepseek]` | `agents/*.toml` | two agents; **Flash kept read-only and its routing contract dropped implementation tasks (proposals as text)**; macOS generated variant uses Keychain command auth |
| env_key portable auth | Utopia-V | same | `env_key = "DEEPSEEK_API_KEY"` | same target | default on Windows/Linux |
| Keychain command auth | Utopia-V | `agents/macos-keychain/v4-flash-worker.toml` | `[model_providers.deepseek.auth]` | manager-generated macOS variant | exact setup Python executable runs the manager's private `_credential-get` helper, preserving Keychain caller identity |

## New in this project (no upstream source)

- Dual-model catalog (`~/.codex/models.json`) and dual registration invariant
- Modality gate (`TEXT_ONLY` / `VISION_TRANSLATABLE` / `VISION_CRITICAL`)
- Visual Context Packet and Evidence Packet schemas
- Flash/Pro routing contracts and FAST/REACT/SPEC/DEEP policy model
- Flash → Pro escalation protocol
- Public Plugin Hook metadata trust check (never forged; Codex records trust itself)
- `doctor` command and handoff-state diagnostics
