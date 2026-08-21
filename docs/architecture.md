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
| Reasoning Adapter | compose policy contract, asymmetric model tuning and acceptance-aware stop condition | select model/policy, rewrite system/tool surfaces, call providers or mutate handoff state |
| Hook | parse `SubagentStart`, claim one-shot handoff, inject first-turn context | call the API or classify the task again |
| Runtime | context budget, sanitizer, adapter, fallback router/client, errors and usage | become the parent decision maker |

## Hook lifecycle

`hooks/hooks.json` is shipped with the Plugin and uses `PLUGIN_ROOT` for all
script paths. `setup` never writes `~/.codex/hooks.json`, `.codex/hooks.json`,
trust hashes or Codex databases. Codex discovers the Plugin Hook and asks the
user to review it. `/hooks` is only a fallback for Codex versions that do not
surface the native prompt.

When the Hook is disabled or untrusted, the parent can call
`runtime/cli.py --mode auto|flash|pro`. The runtime returns a structured error
and the parent continues its task when DeepSeek is unavailable.

## First-turn Reasoning Adapter

The Codex parent remains the only semantic router. After it decides
`agent_type + policy`, `runtime/reasoning.py` performs only this composition:

```text
deepseek_flash | deepseek_pro
        +
FAST | REACT | SPEC | DEEP
        ↓
Policy Contract + optional Model Tuning + Stop Condition
```

The composition is deliberately asymmetric. Flash receives a short reminder
to use supplied evidence directly, obey output and honesty constraints, and do
extra discovery only when a missing fact blocks the answer. Pro receives no
generic model tuning by default; its behavior comes from the selected FAST/REACT/SPEC/DEEP contract
and stop condition. Flash variations keep REACT proposal-only and SPEC as
SPEC-Lite. The route matrix rejects Flash+DEEP both at write time and read
time. Native handoff places the additive blocks in
`SubagentStart.additionalContext` before the first provider request; fallback
renders the same blocks around its bounded task context.

### Acceptance-driven completion

The parent assignment is the source of completion semantics. Delegated
assignments should include `ACCEPTANCE CRITERIA`, `VERIFICATION OWNER`, and a
`STOPPING CONDITION` in their text without adding fields to the envelope. REACT
children implement a complete, robust, idiomatic solution within scope, verify
the criteria within their capability, and surface parent-owned criteria instead
of treating a merely runnable artifact as done. Complex Pro assignments may
explicitly require one bounded QUALITY CLOSURE after the first functional pass;
ordinary Pro assignments are not forced through that extra review.

The Codex parent remains the acceptance gate: it checks the actual artifact,
tests, runtime output, or visual evidence available to it. Missing visual
evidence is `UNVERIFIED`, never an implied pass. Only a successful `completed`
child result enters the normal gate; BLOCKED, interrupted, cancelled, and failed
results are not successful completion. Partial workspace edits are progress,
not completion, and the parent must not duplicate, overwrite, or take over an
active Pro assignment. An objective material engineering gap may trigger at
most one bounded Pro + REACT follow-up; subjective polish does not. This is the
project's information-driven convergence extension, not a new policy, schema
field, transport state, or DSH-native capability.

Because standalone fallback has no native Codex tools, Flash SPEC always
returns an escalation. The fallback parser preserves a provider-supplied
Evidence Packet or maps only the provider's returned structured summary,
findings and evidence into the required packet fields. Native Hook children
retain conditional SPEC-Lite escalation.

`REASONING_ADAPTER_VERSION = 7` is recorded by the evaluation harness. The
Adapter does not add Standard/Spec surfaces, weak/mixed policies, keyword
classification, or any child self-routing.

## DSH capability boundary

The pinned DSH Standard runtime changes the assembled system sections,
clears contexts, limits the first request to shell plus editor, and promotes
the full tool catalog after a durable tool call. Codex's public
`SubagentStart.additionalContext` is additive; it is not a system-section or
pre-request tool-catalog replacement API. This project therefore does not use
"ignore previous instructions", `PreToolUse` denials, or a second
Standard/Spec router to imitate that surface.

Near-field guidance remains a P2 analogy only. It starts with a development
probe that must distinguish DeepSeek Flash child tool events from parent,
Pro, resumed, and parallel turns. No release PostToolUse guidance or
SubagentStop closure is enabled by this change.

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

The transport still permits at most one in-flight handoff per role. Same-role
fan-out requires a separately designed handoff-id/child binding and remains a
transport V2 backlog item; the Reasoning Adapter does not change pending,
claimed, TTL, quarantine, locking or at-most-once semantics.

## Context and output

The Runtime packages task, relevant files, diff, constraints and expected
output under model-specific budgets. Binary and image inputs are withheld;
Codex must provide a parent-generated visual description. Responses use a
stable JSON contract (`summary`, `findings`, `reasoning_summary`, `risks`,
`recommendations`, `confidence`, and observed/inferred/recommended/uncertain
evidence) without requiring chain-of-thought.

Standalone fallback has prompt parity, not capability parity: it has no native
Codex child tool environment and must not claim workspace edits or tests.

## Migration and uninstall

`migrate` removes only an exact legacy router entry identified by its matcher,
command shape and owned script identity. A conflicting entry is reported and
left untouched. `uninstall` removes manager-owned Agents, model catalog and
fallback staging helpers; Plugin Skills and Hook files are removed by Codex's
Plugin uninstall flow. Credentials are retained unless `--remove-credential`
is explicitly passed.

No daemon, database service, Redis, MCP server, proxy or custom trust system is
introduced.
