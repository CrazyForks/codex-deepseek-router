# Multimodal

DeepSeek children are **text-only**. They never receive original visual
inputs — no screenshots, images, video, scanned documents, or diagrams.
DeepSeek never claims to have seen an image.

## Classification

| Class | Meaning | Route |
|---|---|---|
| `TEXT_ONLY` | code, logs, stacktraces, JSON, config, extracted PDF text | DeepSeek directly |
| `VISION_TRANSLATABLE` | the parent can describe the relevant visual facts in text (UI screenshot, error popup, simple diagram, terminal screenshot, describable chart) | Codex Vision → Visual Context Packet → DeepSeek |
| `VISION_CRITICAL` | pixel-perfect comparison, image quality, medical imaging, complex diagram interpretation, artifact detection, repeated screenshot comparison | Parent only; DeepSeek may contribute code/text reasoning |

## Visual Context Packet

Parent-authored facts only:

```text
schema: 1
source_type: screenshot
user_goal: Fix the header alignment.
observations: [viewport ~1440 px, save button is right aligned, ...]
visible_text: [Patients, Save]
relationships: [button center aligns with title vertically]
uncertainties: [exact margin cannot be determined, ...]
source_visibility: parent_only
```

`source_visibility = parent_only` is mandatory: it tells the child the
original image was not sent.

## Child rules (baked into agent TOMLs)

- You cannot see original visual inputs.
- Use only facts explicitly contained in `VISUAL_CONTEXT`.
- Never say "I can see…" or "The screenshot shows…" unless restating a
  parent-supplied observation.
- If required visual information is missing, return
  `NEED_VISUAL_CLARIFICATION` instead of guessing.

## Verification loop

Visual change tasks keep verification with the parent: child edits →
parent inspects the result visually → possible second iteration. The child
never verifies pixels.
