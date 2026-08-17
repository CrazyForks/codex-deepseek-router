# codex-deepseek-router

A thin, reliable routing layer that lets Codex keep itself as the parent
agent while delegating to two native DeepSeek child agents — and choose
between them, and how they reason, based on the task.

```text
Codex Parent (unchanged model/provider)
        │
        ├─ Modality Gate   TEXT_ONLY / VISION_TRANSLATABLE / VISION_CRITICAL
        ├─ Sensitivity Gate  secrets stay local
        ├─ Model Router     deepseek_flash (deepseek-v4-flash) | deepseek_pro (deepseek-v4-pro) | NONE
        ├─ Policy Router    FAST / REACT / SPEC / DEEP
        ├─ Plaintext Handoff (SubagentStart hook: stage -> claim -> deliver -> consume)
        ▼
  DeepSeek child  →  native callback  →  Parent verification & integration
```

What it is **not**: a daemon, proxy, MCP server, database, second Codex CLI,
custom agent runtime, or learned router. V1 is a set of managed files — two
agent TOMLs, one handoff hook, one routing skill, one manager script — with
transactional, rollback-safe installs.

## Quick start

Requirements: Python 3.9+, the Codex desktop app (started at least once),
and a DeepSeek API key.

```bash
git clone https://github.com/<your-org>/codex-deepseek-router.git
cd codex-deepseek-router
python3 codex-deepseek-router/scripts/codex_deepseek_router.py status --json
```

Set the key (stdin only — never argv, files, or chat echo):

```bash
printf '%s\n' '<your-key>' | python3 codex-deepseek-router/scripts/codex_deepseek_router.py setup --api-key-stdin --json
```

The installer:

- installs both agents (`~/.codex/agents/deepseek-flash.toml`,
  `deepseek-pro.toml`) — the DeepSeek provider lives inside them;
- registers both models in `~/.codex/models.json` (never one without the
  other);
- installs the plaintext handoff hook into `~/.codex/hooks.json` (unrelated
  hooks preserved);
- installs the `use-deepseek-router` runtime skill;
- **never touches `config.toml`** — your parent model, provider and ChatGPT
  login stay exactly as they are;
- rolls back on any failure.

Then review the hook in Codex (`/hooks`) — the installer deliberately does
not forge trust — and run the live smoke tests:

```bash
python3 codex-deepseek-router/scripts/codex_deepseek_router.py test --json
```

`test` proves, for **each** agent separately: native `spawn_agent` → DeepSeek
child receives the staged assignment → child replies with a random challenge
marker → `state_*.sqlite` shows `model_provider=deepseek` with the expected
model and agent role. Flash passing never implies Pro passing. A new task /
app restart is required after setup.

On Windows, the agent templates read `DEEPSEEK_API_KEY` from the
environment: set it as a user environment variable and fully restart the
Codex app (the manager stores the key in Credential Manager and injects it
into its own smoke runs).

## Daily use

Codex stays the parent and decides. The `use-deepseek-router` skill guides:

- **Modality first**: text goes to DeepSeek; translatable visuals are
  described by the parent as a Visual Context Packet; critical visual
  judgment stays in the parent.
- **Flash** (`deepseek_flash`) for search, enumeration, logs, extraction,
  code mapping, high-volume reading, and read-only change proposals — it
  never edits files; the parent (or Pro) lands the change.
- **Pro** (`deepseek_pro`) for root cause, architecture, concurrency,
  security, cross-module refactors, and implementation.
- **Policy**: FAST / REACT / SPEC / DEEP, with convergence built in.
- **Escalation**: Flash returns `ESCALATE_TO_PRO` with an evidence packet;
  Pro starts from that evidence instead of re-scanning the repository.
- **No delegation** for trivial, secret-heavy, or purely visual work.

Delegation is one native call after staging:

```text
stage assignment -> spawn_agent(agent_type="deepseek_flash", fork_turns="none") -> wait -> verify -> integrate
```

## Commands

```text
status      read-only state of runtime, agents, catalog, credential, hook
setup       install everything (idempotent, transactional, rollback-safe)
test        live dual-agent smoke tests (both roles, independently)
repair      re-apply after parent-model upgrades / Codex updates / drift
disable     remove only the routing hook; keeps credential + catalog + backups
uninstall   remove everything this project owns; keeps the API key unless
            --remove-credential is passed
doctor      environment + handoff-state diagnostics
```

All commands accept `--json` and `--codex-home`. Exit codes:
`0` ready/configured, `2` action needed (missing credential, conflict, …),
`3` timeout, `1` unexpected failure.

## Documentation

- [references/architecture.md](codex-deepseek-router/references/architecture.md) — component picture
- [references/routing-policy.md](codex-deepseek-router/references/routing-policy.md) — Flash/Pro + FAST/REACT/SPEC/DEEP
- [references/multimodal.md](codex-deepseek-router/references/multimodal.md) — DeepSeek never sees images
- [references/compatibility.md](codex-deepseek-router/references/compatibility.md) — tested baselines and boundaries
- [references/security.md](codex-deepseek-router/references/security.md) — data boundary and key handling
- [docs/architecture.md](docs/architecture.md) — design decisions
- [docs/troubleshooting.md](docs/troubleshooting.md) — symptom → fix
- [docs/eval.md](docs/eval.md) — routing and policy evaluation
- [docs/upstream-reference-map.md](docs/upstream-reference-map.md) — source map for contributors

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q
```

The suite covers manager lifecycle and rollback, the handoff protocol
(claim/consume/quarantine/TTL/cross-role isolation), router contracts,
schema validation, and credential-leak scanning. CI runs on Windows, macOS
and Linux across Python 3.9/3.11/3.12. Live DeepSeek calls are intentionally
not part of CI; run `test --json` manually.

## Attribution

Manager, credential and transaction design adapts
[oil-oil/codex-deepseek-subagent](https://github.com/oil-oil/codex-deepseek-subagent);
the plaintext handoff transport adapts
[Utopia-V/codex-deepseek-subagent](https://github.com/Utopia-V/codex-deepseek-subagent);
reasoning-routing design is inspired by
[yjh051108/dsh-routing-suite](https://github.com/yjh051108/dsh-routing-suite).
See [NOTICE.md](NOTICE.md).

## License

MIT — see [LICENSE](LICENSE).
