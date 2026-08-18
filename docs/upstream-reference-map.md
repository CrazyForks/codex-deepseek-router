# Upstream Reference Map

> First-time source extraction only. Subsequent coding agents read this map
> instead of re-consuming the upstream repositories, unless an implementation
> actually fails (rule AE of the project plan).

Source repositories:

- `oil-oil/codex-deepseek-subagent` (MIT) — manager skeleton
- `Utopia-V/codex-deepseek-subagent` (MIT) — plaintext handoff transport
- `yjh051108/dsh-routing-suite` / `dsh-router-standard` — empirical engineering
  inspiration for model-specific conditioning, first-turn anchoring,
  convergence guidance and model-specific evaluation

## DSH source lock (verified 2026-08-18)

Implementation decisions in this repository use the suite's actual gitlinks,
not the component versions or capability summary shown in its README:

```text
dsh-routing-suite main @ d924ed0fde971255507cefd2fbd311c672e1925d
├── injector @ f4ef59fb31439225abefe45d6e793235a2a9d5e0
└── preset   @ eff787e95132d6c7104214542104a84d656b497e
```

The reviewed preset files are
`preset/router-standard/router-core.mjs`,
`preset/router-standard/router-bootstrap.mjs`, `agent.cordis.yml`, and
`preset.yml` at `eff787e95132d6c7104214542104a84d656b497e`.

| DSH mechanism | Pinned source behavior | This project |
|---|---|---|
| Standard minimal system surface | `router-bootstrap.mjs` keeps only an optional plan section plus a one-sentence persona and clears contexts | Not emulated: `SubagentStart.additionalContext` is additive and must not override Codex instructions |
| Standard first-turn tool surface | First request exposes platform shell plus `str_replace_editor`; a durable `tool/call` promotes the full catalog | Backlog: Codex Hooks do not expose equivalent child-specific pre-request tool-catalog replacement |
| Spec surface | Keeps assembled sections, replaces the persona, and selects a task-conditioned first-turn tool subset | Approximated only through additive policy execution contracts; not named or advertised as DSH Spec mode |
| spec/react/transition/weak bands | `router-core.mjs` quantizes a lightweight keyword classifier; transition is explicitly unstable | Not copied: Codex parent already performs semantic FAST/REACT/SPEC/DEEP routing; no weak/mixed policy is added |
| weak model split | `WEAK_FLASH` carries recall/converge/anti-runaway guidance; `WEAK_PRO` is shorter, and source comments report extra Pro anchors/few-shot variants can hurt | Adopt asymmetric tuning: short Flash anti-repeat/environment guidance; minimal or no generic Pro tuning, decided by ablation |
| first-user classification | `firstUserText` captures the first real `user/message` before assembly, fixing the one-turn lag | Recorded as fixed upstream; no workaround is imported |
| near-field guidance | Real weak-mode user messages receive one simple/complex next-step inbox guide | P2 analogy only: probe Codex child identity first, then at most one Flash-only post-first-tool reminder if reliable and beneficial |
| first-tool promotion | A durable tool call unlocks the full DSH tool catalog | Not copied without an equivalent Codex tool-surface API |
| `dev_router_status` / `dev_router_mode` | Agent can inspect and override its DSH mode | Not adopted; optional diagnostics stay read-only and the Codex parent owns routing |
| mode-isolated subagent | Fresh LLM call runs another DSH reasoning mode | Not adopted; DeepSeek workers cannot self-reroute or recursively construct a second runtime |

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
| child context builder | Utopia-V | same | inline in `run_target_hook_locked` | same target (`build_child_context`) | transport structure retained; first-turn behavior blocks are project-local |
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
- Lightweight Reasoning Adapter (`runtime/reasoning.py`): two model anchors,
  four execution contracts, convergence contract and route-matrix validation
- Baseline-vs-Adapter execution dataset and Evidence Packet comparison harness

## DSH-derived engineering inspiration

The project re-implements, in its own compact language and architecture:

- model-specific behavior conditioning;
- first-turn anchoring;
- convergence and anti-runaway guidance;
- separate Flash/Pro empirical evaluation;
- optional mechanical closure as an experimental idea only.

It does **not** import the DSH runtime/injector, Standard/Spec surface names,
dynamic tool catalog, weak/mixed modes, persona-vector system or learned
router. Near-field remains a separately gated P2 analogy, not a port. The
project also does not adopt retracted or unverified dual-attractor/persona
theories as facts. DSH is an experimental-method and pinned-source reference,
not an explanation of DeepSeek internals.
