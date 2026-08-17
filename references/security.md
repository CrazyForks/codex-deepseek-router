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
  JSON/TOML artifacts, temp files, debug logs, test fixtures, user-facing
  command output, or exception messages. The private `_credential-get`
  helper writes the value only to Codex's captured provider-auth pipe.
- On macOS, Security.framework reads and writes run under the same Python
  executable identity. Agent auth calls the private `_credential-get` helper;
  it does not switch to `/usr/bin/security` and trigger a foreign-app ACL prompt.
- `status`/`doctor` use metadata-only existence checks and output only
  `present: true/false`, never the value.
- Windows/Linux smoke runs inject the key into the `codex exec` environment;
  macOS uses the Keychain helper directly. The value is never logged or
  returned in any payload.
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

The Plugin declares the command Hook and Codex owns discovery, review and
trust. The installer never writes global Hook configuration or forges a trust
record. `/hooks` is fallback UI only; `setup` and `doctor` report the public
Hook metadata and `test` refuses native delivery until Codex reports trust.

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
