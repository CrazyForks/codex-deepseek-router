# Plugin-first Architecture

Codex remains the parent agent and final decision maker. The Plugin owns
discovery and lifecycle; Skills describe behavior; Hooks are thin lifecycle
triggers; Runtime performs context packaging, model selection and explicit
provider execution.

```text
Codex parent
    |
    +-- Plugin manifest (.codex-plugin/plugin.json)
    |       +-- skills/deepseek-router       management
    |       +-- skills/use-deepseek-router   delegation workflow
    |       +-- hooks/hooks.json              native lifecycle trigger
    |       +-- runtime/                      explicit fallback execution
    |
    +-- native agents
            +-- deepseek_flash -> deepseek-v4-flash
            +-- deepseek_pro   -> deepseek-v4-pro
```

## Ownership boundaries

| Layer | Responsibility | Does not do |
|---|---|---|
| Plugin | manifest, Skill/Hook discovery, review/trust UI | store API keys or modify private Codex state |
| Management Skill | setup, status, doctor, migration and uninstall workflow | write Hook config or trust hashes |
| Routing Skill | modality, policy, context and Flash/Pro selection | expose chain-of-thought or bypass parent validation |
| Hook | parse `SubagentStart`, claim one-shot handoff, return context | call the API or build large prompts |
| Runtime | context budget, sanitizer, router, client, errors and usage | become the parent decision maker |

## Hook lifecycle

`hooks/hooks.json` is shipped with the Plugin and uses `PLUGIN_ROOT` for all
script paths. `setup` never writes `~/.codex/hooks.json`, `.codex/hooks.json`,
trust hashes or Codex databases. Codex discovers the Plugin Hook and asks the
user to review it. `/hooks` is only a fallback for Codex versions that do not
surface the native prompt.

When the Hook is disabled or untrusted, the parent can call
`runtime/cli.py --mode auto|flash|pro`. The runtime returns a structured error
and the parent continues its task when DeepSeek is unavailable.

## Parent isolation

DeepSeek provider blocks remain inside the two Agent TOMLs. The manager never
changes the parent's `config.toml`, parent model, provider or login. The model
catalog records both models but does not force a global model switch.

## Handoff state

Each role has an independent one-shot state machine:

```text
absent -> pending -> claimed -> validated -> delivered -> consumed
                       \\-> failed/quarantined
```

Claims are atomic, bounded and TTL-protected. A malformed or expired payload
cannot be delivered to the other role. The Hook catches invalid JSON and
transport failures so a failed DeepSeek handoff does not terminate the parent
task.

## Context and output

The Runtime packages task, relevant files, diff, constraints and expected
output under model-specific budgets. Binary and image inputs are withheld;
Codex must provide a parent-generated visual description. Responses use a
stable JSON contract (`summary`, `findings`, `reasoning_summary`, `risks`,
`recommendations`, `confidence`, and observed/inferred/recommended/uncertain
evidence) without requiring chain-of-thought.

## Migration and uninstall

`migrate` removes only an exact legacy router entry identified by its matcher,
command shape and owned script identity. A conflicting entry is reported and
left untouched. `uninstall` removes manager-owned Agents, model catalog and
fallback staging helpers; Plugin Skills and Hook files are removed by Codex's
Plugin uninstall flow. Credentials are retained unless `--remove-credential`
is explicitly passed.

No daemon, database service, Redis, MCP server, proxy or custom trust system is
introduced.
