# Troubleshooting

Start with `status --json` — it is read-only and safe.

## Setup and configuration

| Symptom | Cause | Fix |
|---|---|---|
| `credential_missing` | no API key stored / no env var | pipe the key through `--api-key-stdin`; never argv or files |
| `unsupported` on Linux setup | no system credential store | set `DEEPSEEK_API_KEY` and rerun |
| `desktop_codex_missing` | runtime not discoverable | start the desktop app once, or set `CODEX_DESKTOP_BIN` |
| `conflict` with a path | existing foreign file at a managed target | decide with the user: keep theirs or move it; never force |
| `legacy_hook_conflict` | an old global matcher is not byte-for-byte ours | inspect it; `migrate --json` refuses it safely |
| `operation_in_progress` | concurrent manager run | wait for the other process to finish |
| `parent_model_unconfigured` | no top-level `model` in config.toml | set a parent model in Codex, then rerun |
| repeated macOS login-password prompts | stale agent auth still calls `/usr/bin/security`, or Python changed after setup | update the Skill, run `repair --json` with the same `python3`, then restart Codex |

## Hook review and handoff

| Symptom | Cause | Fix |
|---|---|---|
| `hook_review_required` in setup/status | Plugin Hook not reviewed yet | restart/open a new task for native review; use CLI `/hooks` only as fallback |
| `hook_untrusted` from `test` | same | review the Plugin Hook, then run `test` again |
| child reports no assignment | stage failed or was skipped | never spawn after a failed stage; re-stage, then spawn |
| `handoff_conflict` / stage exit 3 | pending or quarantined state for that role | let it expire, or inspect `~/.codex/deepseek-router/handoff/` and clear deliberately |
| `handoff_missing` at child start | assignment expired (TTL 300 s) | re-stage immediately before spawning |
| quarantined `failed.*.json` blocks the role | malformed state | inspect and delete the quarantine file only after understanding why it appeared |

## Live tests

| Symptom | Cause | Fix |
|---|---|---|
| `child_start_failed` with hook errors in stderr | Plugin Hook untrusted or unavailable | review the Plugin Hook; run `doctor --json` or explicit runtime fallback |
| `child_timeout` | smoke ran past 300 s | check network/credential; rerun |
| `native_route_mismatch` | marker or sqlite metadata mismatch | read the returned `child_ids`/`metadata`/`expected` details; the child may have answered through the wrong model/provider |
| only one of Flash/Pro passes | they are independent | fix the failing role; never infer Pro from Flash |

## Uninstall and recovery

| Symptom | Cause | Fix |
|---|---|---|
| `not_managed` | no manifest at this codex home | nothing to do; check `--codex-home` |
| uninstall refuses (`conflict`) | a managed file was edited by hand | restore the file or accept the conflict and remove manually |
| credential survives uninstall | by design | pass `--remove-credential` when the user explicitly asks |

## After environment changes

- Parent model upgrade → `repair --json` (refreshes the recorded parent
  snapshot), then `test --json`.
- Codex app update → `repair --json`; trust records remain owned by Codex and
  are reviewed through the Plugin UI, then run `test --json`.
- Python upgrade on macOS/Linux → `repair --json` refreshes the hook
  command paths (recognized as our own entry, not a conflict).
