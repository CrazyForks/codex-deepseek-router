# Routing Policy

The parent (Codex) makes routing decisions semantically; this document is
the decision contract. There is no keyword-scoring router.

## Model routing

### deepseek_flash (deepseek-v4-flash)

Fast bounded read-only worker: repository search, enumeration, logs,
test-output analysis, extraction, code mapping, high-volume reading, and
pre-implementation analysis. Flash never edits files — it returns findings,
analysis or a proposed change as text, and the parent (or Pro) lands the
edit.

Examples: "Find all callers of create_job", "Find the earliest anomaly in
this 5000-line test log", "Propose the exact diff for renaming tmp to
buffer in this function".

### deepseek_pro (deepseek-v4-pro)

Deep solver/reviewer: difficult root cause, architecture, concurrency and
races, distributed consistency, state machines, security analysis,
cross-module refactors, ambiguous behavior, hard implementation.

Example: "Why do lease renew and settlement occasionally race?"

### NO_DELEGATION

Trivial work the parent can finish faster than the handoff cost; strong
visual tasks (`VISION_CRITICAL`); highly sensitive data; anything where
delegation overhead exceeds the task. Delegation needs to materially improve
specialization, context isolation, cost, or quality.

## Reasoning policies

The policy name is not the prompt. `runtime/reasoning.py` composes one of four
Execution Contracts with a policy-specific Stop Condition and, for Flash only,
short model tuning on the child's first request:

| Agent | FAST | REACT | SPEC | DEEP |
|---|---:|---:|---:|---:|
| `deepseek_flash` | yes | read-only proposal | SPEC-Lite | **invalid** |
| `deepseek_pro` | yes | implement/test | root cause | yes |

The same matrix is checked before a pending file is created and whenever an
envelope is read. Invalid combinations are never silently upgraded,
downgraded or rewritten.

- **FAST** — inspect the minimum needed → answer → stop. Search,
  extraction, simple investigation. Flash.
- **REACT** — understand → implement → test → fix → converge. Clear
  requirements, clear path. Pro for implementation; Flash may prepare the
  change as a read-only proposal (diff or plan) for the parent to land.
- **SPEC** — inspect → trace → hypothesis → evidence → root cause →
  smallest fix → verify. Bugs, reviews, unexpected behavior. Pro (Flash may
  run a SPEC-Lite exploration).
- **DEEP** — model system → invariants → failure modes → alternatives →
  decision, with explicit decision closure. Architecture, distributed
  systems, complex concurrency, security, very hard root cause. Pro only.

Every policy converges: reason until there is enough evidence to act, then
commit. Unbounded reasoning, repeated hypotheses and analysis-without-action
are forbidden; an agent that cannot continue returns `BLOCKED` (what is
missing, why, minimal next step).

Agent TOMLs contain only role and safety invariants. Dynamic investigation,
implementation and closure flows live in the Reasoning Adapter so a FAST
request does not inherit an unrelated exhaustive workflow.

Model tuning is intentionally asymmetric. Flash is reminded to use supplied
evidence directly, obey output and honesty constraints, and do extra discovery
only when a missing fact blocks the answer. Pro receives no generic tuning in
adapter version 5: the pinned DSH source reports that additional recall/converge and
few-shot anchors can reduce Pro performance, while this project already has an
explicit parent-selected policy contract. A/B/C ablation may add Pro tuning in
the future only if it earns its cost.

Standalone fallback is more conservative than Native delivery for Flash SPEC:
without a native tool environment it always returns a complete Evidence Packet
for Pro continuation. The adapter may normalize fields already returned by the
provider, but never fabricates edits, commands, tests, or observations.

These policies are project execution contracts; they are not DSH's
spec/react/transition/weak behavior bands. No weak, mixed, continuous mode, or
secondary Standard/Spec router exists here.

## Escalation

Flash returns `ESCALATE_TO_PRO` with an `EVIDENCE_PACKET`
(summary, relevant_files, observations, hypotheses, eliminated,
open_questions, recommended_next_step) when it hits multi-module evidence
conflicts, concurrency, complex state machines, unconfirmable root cause,
architecture tradeoffs, or change risk beyond the task boundary. The parent
passes that packet to Pro instead of making Pro rediscover the repository.

## Policy A/B evaluation

Policies are hypotheses, not dogma: Current vs. Contract-only vs.
Contract+Tuning runs compare correctness first, then tool calls, total tokens,
latency, unnecessary/environment reads, unbounded search, convergence,
root-cause accuracy and code correctness. Flash and Pro are scored separately;
a block whose improvement is below noise gets deleted. See `docs/eval.md`.

The no-Hook fallback accepts an optional `policy`. Without one it uses the
deterministic minimum defaults `flash → FAST` and `pro → REACT`; it does not
claim semantic-policy parity with the Codex parent. Prompt guidance is shared,
but fallback capability is not: it is an explicit text-only provider request
without native subagent tools.
