# Architecture

```text
                     Codex Parent (unchanged model/provider)
                          │
                 ┌────────▼────────┐
                 │ Delegation Gate │  is delegation worth it at all?
                 └────────┬────────┘
                          │
                 ┌────────▼────────┐
                 │ Modality Gate   │  TEXT_ONLY / VISION_TRANSLATABLE / VISION_CRITICAL
                 └────────┬────────┘
                          │
              ┌───────────┴───────────┐
              │                       │
           Text-only               Visual
              │                       │
              │                 Codex Vision (parent)
              │                       │
              │               Visual Context Packet
              └───────────┬───────────┘
                          │
                 ┌────────▼────────┐
                 │ Sensitivity Gate│  secrets stay with the parent
                 └────────┬────────┘
                          │
                 ┌────────▼────────┐
                 │  Model Router   │  FLASH / PRO / NONE
                 └────────┬────────┘
                          │
                 ┌────────▼────────┐
                 │  Policy Router  │  FAST / REACT / SPEC / DEEP
                 └────────┬────────┘
                          │
                  plaintext assignment
                          │
                 ┌────────▼────────┐
                 │   Handoff Hook  │  stage -> claim -> additionalContext -> consume
                 └────────┬────────┘
                          │
              deepseek_flash        deepseek_pro
                          │
                  native callback
                          │
                  Parent verification & integration
```

## Pieces

- **Manager** (`scripts/codex_deepseek_router.py`): single-entry CLI
  (`status setup test repair disable uninstall doctor`). Owns atomic writes,
  backups, manifest, conflict/adoption detection, credentials, catalog,
  agent/hook/skill installation and the dual-oracle smoke tests.
- **Agents**: two standalone TOMLs in `~/.codex/agents/`. Each declares its
  own `model`, `model_provider`, `[model_providers.deepseek]` block and
  sandbox. Codex treats each file as the config layer of the spawned child
  session, so the parent provider is never touched.
- **Transport** (`hooks/plaintext_handoff.py`, `.ps1`): one-shot plaintext
  task delivery. The parent stages a bounded assignment into per-role state
  (`deepseek_flash.pending.json` / `deepseek_pro.pending.json`) under an OS
  lock; a `SubagentStart` command hook claims it atomically and injects it
  as `additionalContext`. Why: the V2 cross-provider spawn message can be
  carried as provider-internal ciphertext that DeepSeek cannot consume
  (openai/codex#34833, #36376), so the assignment travels in plaintext
  instead. At-most-once; TTL 300 s; malformed state is quarantined.
- **Runtime skill** (`skills/use-deepseek-router`): teaches the parent when
  and how to delegate: modality → sensitivity → model → policy → dispatch →
  verify → escalate. Flash is read-only (proposals as text, parent lands
  edits); Pro is workspace-write and owns implementation.
- **State** (`~/.codex/deepseek-router/`): manifest.json, timestamped
  backups, per-role handoff files. All writes are temp-file + fsync +
  atomic replace inside a process lock; failures roll back the transaction.

## Transport modes

1. `native` — Codex's own V2 collaboration message. Preferred once a real
   smoke proves the child receives and understands the assignment.
2. `plaintext_hook` — the installed SubagentStart hook. **V1 default**,
   because native cross-provider delivery is currently unreliable.
3. `legacy_v1` — last-resort whole-session `multi_agent_v2 = false`. Not
   applied automatically; documented only as an explicit workaround.

The manager keeps a `TransportCapabilityProbe`-style contract: transport
selection follows real smoke evidence, never Codex version numbers.

## Failure model

`credential_missing`, `agent_missing`, `provider_error`, `hook_untrusted`,
`handoff_missing`, `handoff_expired`, `handoff_conflict`, `child_start_failed`,
`child_timeout`, `child_cancelled`, `routing_failed`,
`visual_context_insufficient`, `pro_escalation_required`,
`native_transport_broken`, `config_conflict`, `operation_in_progress`.
There is no catch-all "DeepSeek failed".
