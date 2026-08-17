# Security

## Data boundary

DeepSeek children receive the assignment text, related code context and tool
results; that data is transmitted to the DeepSeek provider. The installer
never places `.env` files, tokens, passwords, authentication headers, SSH or
private keys into a handoff. When a task's core input is sensitive
(credentials, personal records, regulated data), the parent handles it
locally instead of dispatching to DeepSeek.

## API key handling

- Stored in macOS Keychain or Windows Credential Manager under
  `io.github.codex-deepseek-router.deepseek-api-key`; Linux V1 uses the
  `DEEPSEEK_API_KEY` environment variable.
- Accepted only through `--api-key-stdin`. Never via argv, config files,
  JSON/TOML artifacts, temp files, debug logs, test fixtures, stdout, or
  exception messages.
- `status`/`doctor` output only `present: true/false`, never the value.
- The manager injects the key into the `codex exec` environment for smoke
  runs; the value is never logged or returned in any payload.
- Tests and CI scan repository artifacts for `sk-…`, `DEEPSEEK_API_KEY=`,
  `Authorization: Bearer` patterns.

## Plaintext handoff risk

The staged assignment briefly exists as plaintext in local user state
(`~/.codex/deepseek-router/handoff/`, mode 0700) before dispatch. The hook
is a transport compatibility layer, not a confidential channel — treat its
contents as already outbound to DeepSeek. State files are per-role,
at-most-once, TTL-bounded, and malformed state is quarantined instead of
delivered.

## Hook trust

Codex requires user review of non-managed command hooks (`/hooks`). The
installer never writes or forges the trust record. `setup` reports
`hook_review_required` until the user reviews the hook; the live `test`
refuses to run before that.

## Ownership and rollback

Every modification happens under a process lock with a timestamped backup,
parse-before-replace validation and atomic writes. Foreign configuration
that conflicts is never silently overwritten; failures roll back to the
pre-transaction state. `uninstall` removes only project-owned files and
keeps the API key unless `--remove-credential` is passed.

## Observability

Logs and result payloads carry only non-sensitive metadata: dispatch id,
agent role, policy, transport mode, duration, status. Never the API key,
full assignments, source code, or environment contents.
