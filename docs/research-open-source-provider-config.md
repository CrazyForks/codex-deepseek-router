# Open-source patterns for persistent provider/base URL configuration

Research date: 2026-08-19

Scope: how open-source tools expose custom OpenAI-compatible endpoints, model IDs,
configuration precedence, and persistence across normal application updates or
regeneration. Sources are first-party documentation or source code.

## Findings

### Aider: layered user/project config plus explicit CLI override

Aider loads `.aider.conf.yml` from the home directory, repository root, and current
directory, in that order; later files take precedence. It also supports an explicit
`--config <filename>` path. The same file can contain `openai-api-base`.

Sources: [Aider YAML configuration docs](https://aider.chat/docs/config/aider_conf.html#yaml-config-file),
[Aider API configuration example](https://aider.chat/docs/config/aider_conf.html#sample-config-file).

Implication: a one-shot CLI flag is not enough for a durable setting. A user-owned
configuration file and documented precedence are the normal pattern.

### Continue: global config is the durable source; model entries own `apiBase`

Continue stores its global configuration under `~/.continue` on macOS (and the
corresponding user profile directory on Windows). Its migration documentation says
`config.yaml` is loaded instead of `config.json` when present, and model entries can
include both an OpenAI-compatible `apiBase` and a model ID.

Sources: [Continue config migration](https://github.com/continuedev/continue/blob/main/docs/reference/yaml-migration.mdx#yaml-config-file),
[Continue example with `apiBase`](https://github.com/continuedev/continue/blob/main/docs/reference/yaml-migration.mdx#models).

Implication: endpoint and model identity belong together in a durable provider/model
record, rather than being inferred from generated runtime files.

### Roo Code: provider profile is a user setting, with URL and model as first-class fields

Roo Code documents an “OpenAI Compatible” provider whose settings include Base URL,
API key, and Model ID. Its settings-management documentation provides import/export
and a configurable persistent storage path for provider profiles and global settings.

Sources: [Roo Code OpenAI-compatible provider](https://github.com/RooCodeInc/Roo-Code/blob/main/apps/docs/docs/providers/openai-compatible.md#general-configuration),
[Roo Code settings management](https://github.com/RooCodeInc/Roo-Code/blob/main/apps/docs/docs/features/settings-management.md#import-and-export-settings).

Implication: the endpoint is user-owned state. Updating application code does not
mean rewriting the user's provider profile; regeneration reads that profile.

### Open WebUI: runtime-editable config persisted in a config store

Open WebUI reads environment defaults for `OPENAI_API_BASE_URL`/`OPENAI_API_BASE_URLS`,
normalizes trailing slashes, and supports multiple base URLs. Its admin endpoint
persists the edited base URLs and associated keys/configs through its `Config` store,
then clears model caches so the new configuration is used immediately.

Sources: [Open WebUI environment defaults](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/config.py),
[Open WebUI persisted provider update](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/routers/openai.py#L2840-L2905).

Implication: defaults and user overrides are separate layers; changing a provider
configuration also invalidates derived model discovery/cache state.

### OpenAI Python: explicit constructor value beats environment, then default

The official OpenAI Python client resolves `base_url` as constructor argument first,
then `OPENAI_BASE_URL`, then the default `https://api.openai.com/v1`. The implementation
also treats endpoint construction as a separate concern from configuration resolution.

Source: [OpenAI Python client initialization](https://github.com/openai/openai-python/blob/main/src/openai/_client.py#L2683-L2695).

Implication: codex-deepseek-router should use one resolver with an explicit precedence
order and pass the resolved value to every request/config generator.

## Synthesis for codex-deepseek-router

The strongest shared pattern is:

1. Keep a small, durable user configuration outside plugin/cache/generated files.
2. Resolve `explicit override -> saved user config -> built-in default`.
3. Treat Agent TOMLs, `models.json`, fallback runtime code, and model discovery as
   projections of that resolved configuration.
4. Keep the provider's base URL and model ID explicit; do not infer them from a
   manually edited generated file.
5. Normalize the URL once (including trailing slash behavior) and validate it at the
   configuration seam.
6. When configuration changes, invalidate or regenerate every derived artifact and
   any cached model-discovery result.
7. Preserve the user configuration across setup, repair, and plugin upgrades. A
   repair should restore projections from the saved configuration, not reset it.

For this repository, the existing `~/.codex/deepseek-router/manifest.json` is a
reasonable durable store because it already lives outside the plugin cache and is
used by setup/repair. Add a versioned user-configuration field (or a narrowly scoped
sidecar if manifest state and desired config must later diverge), and make all
consumers read one `RouterConfig` resolver.
