# Architecture (developer notes)

This document explains the design decisions behind the V1 implementation;
the user-facing picture is in
`codex-deepseek-router/references/architecture.md`.

## Why the installer never writes config.toml

oil-oil's proven approach registers the DeepSeek provider in the top-level
`config.toml` and pins the parent's `multi_agent_version` to `v1` so the
spawn message stays plaintext. This project deliberately does neither:

- The provider block lives inside each agent TOML (Utopia-V pattern): Codex
  treats the agent file as the spawned session's config layer, so the parent
  session keeps its own model/provider/login untouched. This is the
  strongest possible "parent isolation" guarantee and avoids conflicts with
  other tools that manage `config.toml` (catalog switchers, other subagent
  installers).
- Forcing V1 changes the whole parent session's multi-agent implementation
  as a side effect. We keep V2 and solve the actual problem — the
  cross-provider ciphertext carrier — with the one-shot plaintext
  `SubagentStart` hook, which is per-spawn and leaves the session alone.

Consequence: `setup` can install next to oil-oil or Utopia-V installations
without touching their configuration.

## Model catalog

`~/.codex/models.json` is a router-managed dual-model registry (both
`deepseek-v4-flash` and `deepseek-v4-pro`, always together). Current Codex
builds only consult a catalog through the `model_catalog_json` pointer in
`config.toml`; since we never touch that pointer, the registry is inert for
the live runtime (agents self-declare their models) and serves as the
authoritative record, machine-readable model metadata, and the upgrade path
for a future native transport. `codex exec` smoke runs can pass
`-c model_catalog_json=...` as a per-invocation override if needed without
modifying user configuration.

## Transaction system

All mutating commands run under `state_dir/manager.lock` (fcntl/msvcrt
non-blocking with retry) and follow:

```text
lock -> snapshot backup -> generate candidate -> parse/validate ->
atomic replace -> verify -> commit manifest
```

Any failure restores the snapshot and re-raises. Targets: config.toml
(snapshot only), models.json, both agent TOMLs, hooks.json, hook scripts,
runtime skill, manifest. Foreign content that differs from our managed
content is a `conflict`, never overwritten; byte-identical content is
adopted (`adopted_existing`) and restored rather than deleted on uninstall
when it pre-existed.

## Handoff state machine

Per role (`deepseek_flash` / `deepseek_pro`), so one pending assignment per
role and cross-role misdelivery is impossible by construction:

```text
absent -> pending (stage, TTL 300s) -> claimed.<agent_id>.<uuid> (atomic
rename) -> validated -> delivered via additionalContext -> consumed
```

Malformed claims move to `failed.*` quarantine and block the role until a
human resolves them (or TTL expiry cleans structurally valid leftovers).
`stage` refuses to run when a claim/quarantine is active; expired pending
files may be replaced. `os.link` provides no-clobber publish on POSIX; the
PowerShell variant uses exclusive `FileShare.None` handles.

## Smoke oracle

`test` runs one smoke per role. The parent prompt stages a marker-bearing
assignment through the installed handoff script, spawns the child with
`fork_turns="none"`, waits natively, and returns the child's final message.
The oracle requires **both**: the returned message contains the random
marker and the computed result, and `state_*.sqlite` contains a `threads`
row with `model_provider=deepseek`, the expected `model`, and the expected
`agent_role`. Direct DeepSeek HTTP calls are never used as success evidence.
Flash passing never implies Pro passing.

## Failure model mapping

| Surface symptom | Failure code |
|---|---|
| no key anywhere | `credential_missing` |
| hook not reviewed | `hook_untrusted` |
| no pending file at SubagentStart | `handoff_missing` |
| pending expired before claim | `handoff_expired` |
| second stage while pending | `handoff_conflict` (busy) |
| spawn produced no child thread | `child_start_failed` |
| smoke ran past deadline | `child_timeout` |
| marker or metadata mismatch | `native_route_mismatch` |
| foreign config at install target | `config_conflict` |
| concurrent manager run | `operation_in_progress` |

## V1 exclusions (by design)

No daemon, server, database, MCP, proxy, custom agent runtime, learned
router, dynamic tool-schema rewriting, or parallel same-role children. The
transport probe contract keeps a future `native` mode as a drop-in
replacement for the hook.
