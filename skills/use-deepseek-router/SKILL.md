---
name: use-deepseek-router
description: Use the DeepSeek-backed deepseek_flash / deepseek_pro child agents through the installed one-shot plaintext SubagentStart handoff. Use whenever Codex considers spawning, continuing, or troubleshooting these agents; it governs modality classification, Flash/Pro model routing, FAST/REACT/SPEC/DEEP reasoning policy, plaintext staging, native fork_turns=none spawning and return, Flash-to-Pro escalation, one-shot state recovery, and the configured provider/DeepSeek data boundary.
---

# DeepSeek Router

Use DeepSeek only when delegation materially improves cost, context isolation,
parallel exploration, or reasoning quality. The Codex parent stays the main
agent: it decides, verifies, and integrates.

## User-facing language

Write all parent commentary and final responses in the same language as the user's latest request,
unless the user explicitly asks for another language.
Tell the child to answer in that language as part of every assignment, and
translate internal English status codes instead of exposing them as prose.

## Step 1 — Modality

Classify the task as:

- `TEXT_ONLY` — code, logs, JSON, config, extracted text.
- `VISION_TRANSLATABLE` — you can inspect the visual input and express the
  relevant facts as text (UI screenshot, error popup, simple diagram,
  terminal screenshot, chart whose facts can be described).
- `VISION_CRITICAL` — pixel-perfect comparison, image quality, medical
  imaging, complex diagram interpretation, artifact detection, repeated
  screenshot comparison.

Never send original visual inputs to DeepSeek.

For `VISION_TRANSLATABLE`, inspect the visual input yourself and construct a
Visual Context Packet:

```text
source_type, user_goal, observations, visible_text, relationships,
uncertainties, and source_visibility = parent_only
```

For `VISION_CRITICAL`, keep visual reasoning in the parent. DeepSeek may only
contribute code or text reasoning.

## Step 2 — Model

Choose `deepseek_flash` for:

- repository search
- enumeration
- logs
- extraction
- code mapping
- high-volume reading
- pre-implementation analysis and change proposals (diffs/plans as text)

`deepseek_flash` is read-only: it never edits files. The parent lands any
change it proposes, or hands the implementation to `deepseek_pro`.

Choose `deepseek_pro` for:

- difficult root cause
- architecture
- concurrency
- distributed systems
- security
- cross-module reasoning
- complex review
- implementation (simple or difficult)

Do not delegate trivial work whose handoff cost exceeds its value.

## Step 3 — Policy

- `FAST`: minimum investigation -> result -> stop. (Flash)
- `REACT`: understand the requested result -> implement the smallest coherent
  solution that can satisfy the assignment -> test -> check child-verifiable
  acceptance criteria -> fix -> converge. (Pro for implementation; Flash may
  prepare the change as a read-only proposal)
- `SPEC`: inspect -> trace -> hypothesis -> evidence -> root cause ->
  smallest fix -> verify. (Pro; Flash may run a SPEC-Lite exploration)
- `DEEP`: system model -> invariants -> failure modes -> alternatives ->
  decision, with explicit decision closure. (Pro only)

Every DeepSeek agent reasons until there is enough evidence to act, then
commits. If it cannot continue, it returns `BLOCKED` with what is missing,
why, and the minimal next step.

### Assignment contract

Every delegated assignment is a bounded contract, not a general request for
the child to keep improving the workspace. Include these sections whenever
they are relevant:

```text
OBJECTIVE
...

SCOPE
...

EXCLUSIONS
...

PERMISSIONS
...

OUTPUT CONTRACT
...

ACCEPTANCE CRITERIA
- ...
- ...

VERIFICATION OWNER
CHILD | PARENT | SHARED

STOPPING CONDITION
...
```

`ACCEPTANCE CRITERIA` state what must be true for the requested result to be
complete. `STOPPING CONDITION` states what evidence permits the child to
return. `VERIFICATION OWNER` is assignment text only: use `CHILD` for
deterministic checks the child can run, `PARENT` for parent-only evidence such
as screenshots or sensitive final judgment, and `SHARED` when the child checks
functionality while the parent checks final quality.

For ordinary functional work, keep the criteria concrete and short:

```text
ACCEPTANCE CRITERIA
- endpoint exists at the requested path
- documented status code and response body are returned
- relevant tests pass without regressions

VERIFICATION OWNER
CHILD
```

For root-cause or architecture work, require evidence and invariant closure:

```text
ACCEPTANCE CRITERIA
- root cause is supported by concrete evidence
- material alternative explanations are addressed
- the relevant invariant is preserved by the proposed fix
- a verification plan or actual verification is reported

VERIFICATION OWNER
SHARED
```

For visual, UI, or WebGL work, translate the user's requested result into
observable criteria instead of treating “it runs” as completion. Keep visual
judgment parent-owned or shared, and never ask a text-only child to inspect an
original screenshot.

Example for a high-precision interactive black-hole request:

```text
ACCEPTANCE CRITERIA
Functional:
- single HTML/JS artifact runs in a modern browser
- mouse interaction works and animation remains continuous

Visual / physical:
- black-hole shadow is visually clear
- gravitational lensing is obvious
- the rear accretion disk is visibly folded/lensed around the shadow
- the accretion disk reads as a disk, not merely a glowing torus
- left/right Doppler brightness asymmetry is clearly visible
- the result is immediately recognizable as a striking black-hole simulation

VERIFICATION OWNER
SHARED
```

The runtime validates the route contract before staging and again when an
envelope is read. `deepseek_flash + DEEP` is an error: do not silently change
the policy or role; stage `deepseek_pro + DEEP` explicitly if that is the
parent's semantic decision.

## Step 4 — Dispatch

1. Decide whether delegation is useful.
2. Check modality.
3. Check sensitivity: API keys, `.env`, private keys, credentials, tokens,
   personal records, regulated data. When the core task requires such data,
   the parent handles it locally instead of dispatching to DeepSeek.
4. Select Flash or Pro.
5. Select the reasoning policy.
6. Build one bounded, self-contained assignment (objective, scope,
   exclusions, permissions, output contract, acceptance criteria,
   verification owner, stopping condition).
7. Add the Visual Context Packet if needed.
8. Stage the complete assignment through the installed handoff script in
   `stage` mode, then spawn with the exact agent type and
   `fork_turns="none"`:
   - stage: `python3 <codex-home>/hooks/codex-deepseek-router/plaintext_handoff.py --mode stage --agent-type <role> --policy <POLICY> --modality <MODALITY> --state-directory <codex-home>/deepseek-router/handoff` with the assignment on stdin. This is only a local staging helper; the SubagentStart Hook is supplied by the Plugin.
   - spawn: `spawn_agent(agent_type="deepseek_flash|deepseek_pro", fork_turns="none")`
9. Receive the child through Codex's native wait/callback path. Do not
   short-poll or re-run the child work while it runs.
10. Validate the returned contribution in proportion to the claim.
11. Integrate the final answer in the parent.

## Hook-disabled fallback

When Plugin Hooks are unavailable or untrusted, make an explicit text-only
request through the standalone runtime:

```text
printf '%s' '{"task":"...","context":{},"policy":"FAST"}' | python3 <plugin-root>/runtime/cli.py --mode auto
```

Use `--mode flash` or `--mode pro` for an explicit user choice. Structured
provider failures are advisory; Codex continues the parent task and remains
the final decision maker. If `policy` is omitted, fallback deterministically
uses `FAST` for Flash and `REACT` for Pro; it does not run a second semantic
policy classifier. The fallback shares the same Policy Execution Contract,
Flash-only model tuning (when applicable), and Stop Condition as native
first-turn handoff, but it remains text-only and does not have the native Codex
subagent tool environment. Its Flash SPEC result therefore always becomes a
complete Evidence Packet for Pro continuation; missing wrapper fields are
normalized only from the provider's returned structured content. Pro receives
no generic model tuning by default.

Require a successful stage result naming the exact role before spawning.
Treat a lock contender, an active pending or claimed item, quarantined
state, or any other non-success result as a transport failure. Never spawn
after a failed stage. Delivery is one-shot and at-most-once: never assume a
claimed assignment can be replayed or delivered to a replacement child.

## Step 5 — Escalation

If Flash returns `ESCALATE_TO_PRO`:

1. preserve its Evidence Packet;
2. require `summary`, `relevant_files`, `observations`, `hypotheses`,
   `eliminated`, `open_questions`, and `recommended_next_step`;
3. treat `observations` as reproducible facts, not conclusions;
4. do not ask Pro to rediscover the whole repository;
5. dispatch the evidence plus only necessary context to Pro. Pro expands
   reading only when the packet is incomplete, conflicting, stale, or cannot
   support the next decision.

If DeepSeek returns `NEED_VISUAL_CLARIFICATION`, inspect the visual input
yourself and re-dispatch with the missing facts; do not let the child guess.

## Parent acceptance gate

When the child returns, the parent verifies the assignment rather than merely
accepting the child's completion summary:

1. Check the actual artifact, tests, runtime output, or other evidence that is
   available to the parent.
2. Mark parent-owned criteria `UNVERIFIED` when the required evidence (for
   example, a browser render or screenshot) is not available. Do not claim
   visual or quality acceptance without that evidence.
3. Finish when all material criteria are satisfied or an honest blocked/
   unverifiable limitation is reported.
4. Only when a material gap directly violates an explicit user requirement,
   issue one additional bounded `deepseek_pro + REACT` assignment. Do not
   follow up for optional polish, extra parameters, or subjective tweaks the
   user did not request.

The automatic follow-up allowance is at most one. After the second parent
review, stop and report any remaining limitation; no runtime retry counter or
follow-up state machine is added.

Use this structure for that one follow-up:

```text
OBJECTIVE
Refine the existing implementation only for the material acceptance gaps below.

CURRENT BASELINE
Use the existing implementation as the baseline. Do not restart from scratch.

PASSED CRITERIA
- ...

MATERIAL GAPS
1. ...

DO NOT CHANGE
- ...

VERIFICATION TARGET
- ...

STOPPING CONDITION
Stop after the listed material gaps are addressed and the affected behavior is verified.
```

The follow-up must name the gaps and the verification target; “optimize it
again” is not a bounded assignment. Preserve passed criteria and working
transport/runtime behavior.

## Data boundary

DeepSeek receives the assignment text, related code context and tool results,
and returns them to the provider. Never place `.env` files, tokens,
passwords, authentication headers, SSH or private keys in the handoff. The
staged assignment briefly exists as plaintext in local user state; the hook
is a transport compatibility layer, not a confidential channel.
