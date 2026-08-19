# Evaluation

Routing decisions are made by the Codex parent through the
`use-deepseek-router` skill; the datasets below measure how well that
contract performs, not a learned classifier.

## Datasets

In `tests/fixtures/`:

| Group | File | Size | Bar |
|---|---|---|---|
| A — Flash advantage | `eval-flash-advantage.json` | 20 | ≥ 80% → FLASH (read-only analysis/proposals) |
| B — Pro advantage | `eval-pro-advantage.json` | 20 | ≥ 85% → PRO |
| C — Multimodal | `eval-multimodal.json` | 10 | 10/10 correct `expected_modality` |
| D — No-delegation | `eval-no-delegation.json` | 10 | 10/10 → NONE |

Flash tasks are read-only by contract (proposals, extraction, mapping); any
task that edits files belongs to Pro (REACT) or to the parent.

## Protocol

1. For each task in a group, ask the Codex parent (with the runtime skill
   loaded) for its routing decision:
   `{"agent": "FLASH|PRO|NONE", "policy": "FAST|REACT|SPEC|DEEP",
   "modality": "TEXT_ONLY|VISION_TRANSLATABLE|VISION_CRITICAL", "reason": ...}`.
2. Record one JSONL line per task. Do not let the parent see the expected
   labels.
3. Score: agent accuracy (A/B/D), modality accuracy (C). For A/B, `NONE` is
   also wrong; for D, any spawn is wrong.
4. Disagreements are reviewed by a human; a routing label is changed only
   when the dataset itself is wrong, never to make numbers look good.

## Execution Golden Tasks

`eval/execution-golden-tasks.json` contains 16 concrete, self-contained tasks:
four each for FAST, REACT, SPEC and DEEP. The set covers direct repository
lookup, config/log extraction, Flash read-only proposals, bounded fixes,
lifecycle/root-cause analysis, fencing, idempotency, security boundaries and
transport architecture closure. Three Flash SPEC-Lite cases require a
structured `ESCALATE_TO_PRO` packet.

Validate the dataset and render all 48 A/B/C prompts without provider calls:

```bash
python3 scripts/run_execution_eval.py --variant all
python3 scripts/run_execution_eval.py --variant all --smoke
python3 scripts/run_execution_eval.py --evidence-packet
```

Add `--live` only when billable DeepSeek calls are intended. Output is JSONL
and records only public provider usage. Hidden chain-of-thought is neither
requested nor estimated.

## Current / Contract / Tuning ablation

`eval/baseline-3aa3bf2.json` freezes the pre-Adapter main commit, pytest count,
Native child context, fallback prompt, Flash/Pro TOML instructions, routing
dataset bars and transport invariants. The execution harness compares:

- **A — `current`**: frozen static Agent instructions plus the
  `REASONING_POLICY` label;
- **B — `contract_only`**: dynamic policy execution contract plus stop
  condition, without model tuning;
- **C — `contract_tuning`**: B plus short Flash-specific tuning. Pro C is
  intentionally identical to Pro B because extra generic Pro anchors are not
  enabled without evidence.

Run paired live trials with:

```bash
python3 scripts/run_execution_eval.py --variant all --smoke --live --output smoke.jsonl
python3 scripts/run_execution_eval.py --variant all --repetitions 3 --live --output results.jsonl
```

The harness records `adapter_version`, repetition, correctness, public
input/output tokens, latency, guidance characters and estimated added prompt
tokens. It also reserves explicit fields for tool calls, reads, duplicate
reads, environment checks, repository-wide searches, unbounded searches,
time-to-first-edit and parent rework. Those tool-behavior fields remain null in
standalone fallback because it has no native tool trace; they must be populated
from Native Codex runs rather than invented as zeros.

A block is retained only when correctness does not regress and at least one
benefit is stable beyond run-to-run noise. If cost is constrained, the eight
task `--smoke` set (two per policy) is the first gate; full 16-task repeated
runs follow only after smoke correctness passes.

Flash and Pro results are analyzed separately. The key comparison for Flash is
B→C: does tuning reduce repeated work, environment ceremony or unbounded
search without harming correctness? For Pro, B and C are equal in adapter v5;
generic Pro tuning was removed rather than retained for symmetry. A useful
FAST stop condition does not justify a DEEP contract that lowers correctness.

## Evidence Packet evaluation

The three `evidence_escalation` tasks compare a direct Pro SPEC run with Flash
SPEC-Lite followed by Pro using the returned packet:

```bash
python3 scripts/run_execution_eval.py --evidence-packet --live > evidence-results.jsonl
```

Compare total tokens, latency, correctness, duplicate discovery and parent
integration effort. If Flash consistently adds cost without reducing Pro
rediscovery, narrow the escalation scenarios rather than preserving the flow
for architectural symmetry.

The explicit text-only fallback cannot perform native tool-based SPEC work, so
Flash SPEC always hands its bounded analysis to Pro. If the provider omits the
packet wrapper, the adapter maps only its returned summary, findings, and
evidence into the versioned packet fields; it does not invent edits, tests, or
tool observations. Native Hook delivery remains conditionally escalated by the
Flash SPEC contract.

The 2026-08-18 smoke measurements and their limitations are recorded in
`eval/results/2026-08-18-summary.md`. The reported seven failures in twelve Pro
requests came from the standalone `run_execution_eval.py --live` HTTP harness,
which calls `DeepSeekClient(...).complete()` directly. They are not a failure
rate for the Codex Native Agent path and must not be used to change Native Agent
timeouts or to claim that Native Pro is unavailable. The standalone result is
still gated for API-level follow-up until timeout/network telemetry is separated;
Native release acceptance requires a separate Hook-backed Codex smoke and trace.

## Native quality-sensitive regression

`eval/native-quality-tasks.json` freezes three native-only acceptance cases:
`visual-black-hole`, `responsive-ui`, and `interactive-canvas`. The black-hole
prompt is intentionally fixed rather than softened to fit an implementation.
Each case records functional and visual acceptance criteria plus
`verification_owner: SHARED`: the child verifies deterministic runtime
behavior, while the parent verifies the rendered artifact.

Run these through the real Native Codex path (`stage` → `SubagentStart` →
`spawn_agent` → callback) with two or three repetitions per variant when the
environment can capture browser renders. Record child/parent duration, tool
calls, reads, edits, runtime success, parent follow-up, final artifact and
screenshot path. Compare anonymized screenshot pairs (A old, B current, C
acceptance-driven) on the original user requirements; do not reduce the result
to a single fixed 0/1/2 score or a fabricated visual score.

The acceptance-driven variant is retained only if it improves or preserves
material user acceptance, does not regress ordinary REACT tasks, and keeps
efficiency benefits within observed run-to-run variation. A missing screenshot
is `VISUAL_QUALITY_UNVERIFIED`, not evidence of a pass.

## Multimodal truth boundary (Epic 20)

Use a fixture where the parent supplies "the button is 40px below the
title". Pass that Visual Context to a child and check it: uses the fact,
does not claim to have seen the image, and returns
`NEED_VISUAL_CLARIFICATION` for a fact that was deliberately withheld.

## Tool surface (backlog, not an emulation)

Pinned DSH code makes first-turn system/tool surface a core experimental
variable, but V1 does not emulate it. `PreToolUse` denial happens after the
model has already seen and selected a tool and is not equivalent to hiding its
schema. Revisit only if Codex exposes stable child-specific pre-request tool
catalog control.

## Deliberately excluded V1 experiments

- No release PostToolUse near-field injection. P2 begins with a development
  probe of model/turn/parent/Pro/resume/parallel discrimination; only a reliable
  and beneficial probe may advance to one Flash-only reminder after the first
  durable tool result.
- No dynamic tool-catalog hiding: denying an already exposed tool is not the
  same behavior.
- No same-role multi-Flash fan-out: the current per-role pending state cannot
  correlate multiple children safely.
- Mechanical `SubagentStop` closure remains off unless separate A/B evidence
  shows that one bounded continuation reduces incomplete structured outputs.

P2 compares C against D (C plus one Flash one-shot reminder) and never bundles
near-field and closure into the A/B/C test. `stop_hook_active` is mandatory if
mechanical closure is ever tested.
