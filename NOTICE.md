# NOTICE

This project incorporates and adapts implementation ideas and MIT-licensed
code from:

- [oil-oil/codex-deepseek-subagent](https://github.com/oil-oil/codex-deepseek-subagent)
  (MIT) — manager CLI structure, atomic-write/backup/manifest configuration
  transactions, macOS Keychain and Windows Credential Manager credential
  handling, Codex runtime discovery, and the dual-oracle native subagent smoke
  test concept.

- [Utopia-V/codex-deepseek-subagent](https://github.com/Utopia-V/codex-deepseek-subagent)
  (MIT) — the V2 one-shot plaintext `SubagentStart` handoff transport
  (stage / claim / consume / TTL / quarantine / file locking), the PowerShell
  hardened variant of that protocol, and the standalone custom-agent TOML
  provider configuration pattern.

Reasoning-routing design is additionally inspired by:

- [yjh051108/dsh-routing-suite](https://github.com/yjh051108/dsh-routing-suite)
  — the idea of routing between bounded reasoning policies
  (FAST / REACT / SPEC / DEEP), re-implemented here as decision contracts for
  the Codex parent rather than by copying its assumptions about internal
  provider mechanisms.

## Upstream license notices

Both `oil-oil/codex-deepseek-subagent` and `Utopia-V/codex-deepseek-subagent`
are distributed under the MIT License. The full MIT license text is included
in [LICENSE](LICENSE). Where source code from those repositories has been
adapted, the corresponding copyright notices are preserved in the file
headers of the affected files:

- `scripts/codex_deepseek_router.py` —
  adapted from `oil-oil/codex-deepseek-subagent`
  `codex-deepseek-subagent/scripts/codex_deepseek.py` (MIT).
- `hooks/plaintext_handoff.py` and
  `hooks/plaintext-handoff.ps1` —
  adapted from `Utopia-V/codex-deepseek-subagent`
  `hooks/plaintext_handoff.py` and `hooks/plaintext-handoff.ps1` (MIT).

A per-symbol source map is maintained in
[docs/upstream-reference-map.md](docs/upstream-reference-map.md).
