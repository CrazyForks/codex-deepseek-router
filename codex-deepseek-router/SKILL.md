---
name: codex-deepseek-router
description: Install, configure, check, test, repair, disable or uninstall the codex-deepseek-router suite — two native DeepSeek child agents (deepseek_flash on deepseek-v4-flash, deepseek_pro on deepseek-v4-pro) plus the plaintext handoff transport and the runtime routing skill. Use only when the user asks to manage this router's installation. Do not trigger for ordinary DeepSeek API questions or everyday coding tasks.
---

# Codex DeepSeek Router

This skill maintains the router installation. It never carries out everyday
user tasks. Deterministic file, catalog, credential, hook and smoke-test
operations go through `scripts/codex_deepseek_router.py`; never hand-edit the
TOML/JSON agent files, `hooks.json`, the model catalog or system credential
stores.

## Key contracts

- Codex stays the parent agent. The parent model and provider are never
  changed; the DeepSeek provider lives inside the two agent TOMLs only.
- Both agents are installed simultaneously:
  `deepseek_flash` → `deepseek-v4-flash` (fast worker) and
  `deepseek_pro` → `deepseek-v4-pro` (deep solver). There is no Flash/Pro
  model switching via repair.
- DeepSeek children are text-only. Never send original images, screenshots or
  video to them; the parent inspects visual inputs and passes text facts.
- Task delivery uses the plaintext `SubagentStart` handoff hook. The hook must
  be reviewed by the user with `/hooks`; the installer never forges trust.
- Everyday delegation is a single native call:
  `spawn_agent(agent_type="deepseek_flash" | "deepseek_pro", fork_turns="none")`
  after staging the assignment with the handoff script. Do not use the
  manager script or `codex exec` to carry out user tasks.
- If the current tool schema does not know the DeepSeek roles, tell the user
  to open a new task or restart Codex; do not fall back to another role,
  script, or `codex exec` for the current task.

## After triggering

1. Run `status --json` and continue from the structured state; never guess
   from file names.
2. First-time configuration runs `setup --json`. When no API key is present
   the command returns `credential_missing`: ask the user briefly for the
   key, then pass it only through stdin:
   `printf '%s\n' '<key>' | python3 <skill-dir>/scripts/codex_deepseek_router.py setup --api-key-stdin --json`.
   Never echo, restate or write the key anywhere.
3. `setup` does not run live smoke tests. Run `test --json` afterwards; it
   verifies both agents through the desktop Codex runtime (native
   `spawn_agent` → DeepSeek child → callback) and checks the child thread
   metadata (`model_provider=deepseek`, correct `model`, correct
   `agent_role`) plus a random challenge marker. Flash passing never implies
   Pro passing — both must pass separately.
4. If the result carries `hook_review_required`, tell the user to run `/hooks`
   and review/trust the hook before running `test`.
5. If the result carries `restart_required` or `new_task_required`, tell the
   user to restart the Codex desktop app and open a new task.
6. After a parent model upgrade or a Codex update, run `repair --json`.
7. Final reports state only status, provider, model, roles, hook trust state
   and backup location; never output keys or raw event logs.

## Commands

Entry point (`python3` on macOS/Linux, `py -3` on Windows):

```text
python3 <skill-dir>/scripts/codex_deepseek_router.py <command> --json
```

- `status` — read-only check of runtime, agents, catalog, credential, hook
  and parent isolation.
- `setup` — install both agents, the dual-model catalog, the runtime routing
  skill and the plaintext handoff hook; never touches the parent model or
  provider. Requires the API key via `--api-key-stdin` when missing.
- `test` — live dual-agent smoke tests through the desktop Codex runtime.
- `repair` — idempotently re-apply the managed configuration and refresh the
  recorded parent snapshot (use after parent model upgrades).
- `disable` — remove only the routing hook entry; keep credentials, catalog,
  agents and backups.
- `uninstall` — remove everything this project owns; the API key is kept
  unless `--remove-credential` is passed.
- `doctor` — diagnostics for the environment, hook trust and handoff state.

Use the current `CODEX_HOME` by default; pass `--codex-home` only when the
user explicitly manages another Codex home. On machines where the Codex
runtime is not discoverable, set `CODEX_DESKTOP_BIN` to the bundled `codex`
binary.

## Statuses

- `ready` — both live smokes passed (native spawn, callback, metadata,
  markers).
- `configured` — static configuration complete; live tests not run yet.
- `partial` / `not_installed` — read `errors` and the structured checks.
- `disabled` — routing hook removed; `repair` restores it.
- `credential_missing` — ask for the API key, then continue the flow.
- `operation_in_progress` — another configuration operation is running; wait
  and retry, never modify concurrently.
- `conflict` — report the conflicting file/field and wait for the user's
  decision; never overwrite silently.
- `hook_untrusted` — the user must review the hook with `/hooks`.
- `unsupported` — report the missing capability; do not bypass it by hand.

More detail: [references/compatibility.md](references/compatibility.md),
[references/routing-policy.md](references/routing-policy.md),
[references/multimodal.md](references/multimodal.md).
