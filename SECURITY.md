# Security

## Data boundary

DeepSeek children receive the assignment text, related code context and tool
results; that data is transmitted to the DeepSeek provider
(`https://api.deepseek.com` by default, or the user-configured `base_url`).
Users are responsible for trusting and securing a custom endpoint. The router never places `.env` files, tokens,
passwords, authentication headers, SSH keys or private keys into a handoff,
and the runtime skill keeps sensitive or `VISION_CRITICAL` work in the
parent. See
`references/security.md` for the full model.

## Credential handling

- macOS: Keychain generic password
  (`io.github.codex-deepseek-router.deepseek-api-key`), read/written through
  Security.framework by the same Python identity; status checks metadata only.
- Windows: Credential Manager (same target); agent TOMLs use the
  `DEEPSEEK_API_KEY` environment variable because command-backed credential
  lookup fails under the Desktop sandbox identity (see
  `references/compatibility.md`).
- Linux: `DEEPSEEK_API_KEY` environment variable.

Keys are accepted only via `--api-key-stdin`. They never appear in argv,
config files, JSON/TOML artifacts, temp files, debug logs, test fixtures,
user-facing command output, or exception messages. The private macOS
`_credential-get` helper writes the key only to Codex's captured provider-auth
pipe; `status`/`doctor` report presence only.

## Plaintext handoff

The staged assignment briefly exists as plaintext in local user state
(`~/.codex/deepseek-router/handoff/`, mode 0700) before dispatch. The hook
is a transport compatibility layer, not a confidential channel: treat staged
content as already outbound to DeepSeek.

## Hook trust

The Plugin declares the command Hook and Codex owns discovery, review and
trust. This project never writes global Hook configuration or forges the trust
record. `setup` reports `hook_review_required` until Codex reports trust; the
interactive CLI `/hooks` command is a fallback when the native Plugin UI is
not shown.

## Reporting

Please report security issues privately to the repository maintainers, with
steps to reproduce and the affected platform/Codex version. Do not include
API keys in reports.
