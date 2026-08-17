---
name: deepseek-router
description: Configure, diagnose, test, repair, migrate or uninstall the codex-deepseek-router plugin and its DeepSeek Flash/Pro agents. Use only for router installation management, not everyday delegation.
---

# Codex DeepSeek Router Management

The plugin owns Skill and Hook discovery. This management skill configures
credentials, the two native Agent TOMLs and their model catalog; it never
writes Hook configuration or trust state.

## Workflow

1. Run `python3 <plugin-root>/scripts/codex_deepseek_router.py status --json`.
2. For first-time configuration, run `setup --json`. If it returns
   `credential_missing`, pass the API key only through standard input with
   `--api-key-stdin`; never echo it or put it in argv.
3. If legacy state is reported, run `migrate --json`. Migration removes only
   a byte-for-byte recognized legacy router Hook and keeps unrelated Hooks.
4. Restart Codex or open a new task so Plugin Skills, Agents and Hooks are
   rediscovered. Codex normally presents its native Hook review UI. Use the
   interactive CLI `/hooks` command only when that prompt does not appear.
5. Run `test --json` to verify Flash and Pro independently.

## Contracts

- Codex remains the parent and final decision maker.
- `deepseek_flash` uses `deepseek-v4-flash`; `deepseek_pro` uses
  `deepseek-v4-pro`.
- DeepSeek is text-only. Codex must translate visual inputs into explicit text.
- Hook trust is owned exclusively by Codex. Never modify trust databases,
  hashes or private state.
- Plugin Hook failure is fail-open for the parent task. Explicit routing is
  available through the `use-deepseek-router` skill even when automatic Hook
  integration is unavailable.

## Commands

- `status`: read-only configuration and Plugin/legacy status.
- `setup`: configure credentials, Agents and the model catalog.
- `doctor`: diagnose Plugin, credentials, Agents, connectivity and legacy state.
- `test`: verify Flash and Pro separately.
- `repair`: idempotently refresh managed non-Hook assets.
- `migrate`: precisely remove a recognized legacy global Hook.
- `uninstall`: remove manager-owned non-Plugin state; preserve credentials by
  default.

Final reports include only status, provider, models, roles, Plugin Hook state
and backup location. Never include credentials or raw event logs.
