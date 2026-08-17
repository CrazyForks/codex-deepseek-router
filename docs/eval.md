# Evaluation

Routing decisions are made by the Codex parent through the
`use-deepseek-router` skill; the datasets below measure how well that
contract performs, not a learned classifier.

## Datasets

In `tests/fixtures/`:

| Group | File | Size | Bar |
|---|---|---|---|
| A — Flash advantage | `eval-flash-advantage.json` | 20 | ≥ 80% → FLASH |
| B — Pro advantage | `eval-pro-advantage.json` | 20 | ≥ 85% → PRO |
| C — Multimodal | `eval-multimodal.json` | 10 | 10/10 correct `expected_modality` |
| D — No-delegation | `eval-no-delegation.json` | 10 | 10/10 → NONE |

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

## Policy A/B (Epic 22)

For FAST/REACT/SPEC/DEEP, run paired trials with the policy block stripped
(baseline) vs. applied, on the same tasks, measuring: task success,
tool-call count, total tokens, latency, unnecessary reads, convergence
(hung/looping runs), root-cause accuracy, code correctness. A policy whose
improvement is below noise is removed — no keeping complexity for its own
sake.

## Multimodal truth boundary (Epic 20)

Use a fixture where the parent supplies "the button is 40px below the
title". Pass that Visual Context to a child and check it: uses the fact,
does not claim to have seen the image, and returns
`NEED_VISUAL_CLARIFICATION` for a fact that was deliberately withheld.

## Tool anchoring (Epic 23, P2/experimental)

V1 does not implement first-turn tool-schema anchoring. If a future
benchmark shows clear gains from exposing A/B/C tools in the first turn and
restoring the full set afterwards, revisit then.
