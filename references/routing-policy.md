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

## Escalation

Flash returns `ESCALATE_TO_PRO` with an `EVIDENCE_PACKET`
(summary, relevant_files, observations, hypotheses, eliminated,
open_questions, recommended_next_step) when it hits multi-module evidence
conflicts, concurrency, complex state machines, unconfirmable root cause,
architecture tradeoffs, or change risk beyond the task boundary. The parent
passes that packet to Pro instead of making Pro rediscover the repository.

## Policy A/B evaluation

Policies are hypotheses, not dogma: baseline vs. policy runs compare task
success, tool-call count, total tokens, latency, unnecessary reads,
convergence, root-cause accuracy and code correctness. A policy whose
improvement is below noise gets deleted. See `docs/eval.md`.
