"""Policy contracts and asymmetric model tuning for DeepSeek workers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional


REASONING_ADAPTER_VERSION = 5

FLASH_AGENT = "deepseek_flash"
PRO_AGENT = "deepseek_pro"

VALID_ROUTE_MATRIX: Dict[str, FrozenSet[str]] = {
    FLASH_AGENT: frozenset({"FAST", "REACT", "SPEC"}),
    PRO_AGENT: frozenset({"FAST", "REACT", "SPEC", "DEEP"}),
}
# Compatibility name used by the transport while the matrix remains owned here.
ROUTE_CONTRACTS = VALID_ROUTE_MATRIX
POLICIES = frozenset({"FAST", "REACT", "SPEC", "DEEP"})

FAST_CONTRACT = (
    "Find the minimum direct evidence needed to answer the bounded assignment. Do not expand into "
    "unrelated architecture or equivalent searches; return the supported answer once sufficient."
)

PRO_REACT_CONTRACT = (
    "Understand only the context needed for the smallest coherent change, implement it, run the "
    "minimum relevant verification, fix any resulting failure, and stop. Do not widen scope or build "
    "frameworks, scaffolding, or ceremony the parent did not request."
)

FLASH_REACT_CONTRACT = (
    "Locate the exact change and its constraints, then return a precise read-only proposal with "
    "affected files, patch or diff, and suggested tests. Do not modify the workspace or claim that a "
    "proposed edit or verification was executed."
)

PRO_SPEC_CONTRACT = (
    "Inspect and trace the behavior, form distinct candidate hypotheses, test them against evidence, "
    "eliminate material alternatives, establish the root cause, then give the smallest fix or "
    "recommendation and verify it where possible. Separate observations from inferences."
)

FLASH_SPEC_CONTRACT = (
    "Trace the bounded path, collect reproducible evidence, form limited hypotheses, and eliminate "
    "obvious candidates. Conclude when supported; for concurrency, distributed invariants, fencing, "
    "security boundaries, complex architecture, conflicting modules, or edit-dependent verification, "
    "return ESCALATE_TO_PRO with a complete Evidence Packet."
)

DEEP_CONTRACT = (
    "Model the system, identify invariants and material failure modes, compare only relevant "
    "alternatives, decide, act or recommend, verify, and stop. Depth is information-driven: once the "
    "available evidence distinguishes the main alternatives, move to the decision."
)

FLASH_TUNING = (
    "Use the supplied evidence directly and obey the assignment's requested output and honesty "
    "constraints. Do extra discovery only when a missing fact blocks the answer. Keep the response "
    "focused; when the policy requires escalation, return the required Evidence Packet promptly."
)

# Pinned DSH source reports that extra generic recall/converge anchors can hurt Pro.
# Policy contracts carry Pro behavior; the C ablation intentionally adds no Pro tuning.
PRO_TUNING_MINIMAL = ""

STOP_CONDITIONS = {
    "FAST": "Stop when direct evidence supports the answer and no unresolved issue can materially change it.",
    "REACT": "Stop when the smallest coherent change or proposal is complete and its required verification is reported honestly.",
    "SPEC": "Stop after one root cause is supported, material alternatives are eliminated, and the fix or recommendation is verified where possible.",
    "DEEP": "Stop when information is sufficient to distinguish the main alternatives and further analysis would add completeness without changing the decision.",
}

FLASH_SPEC_STOP = (
    "If the supplied evidence involves concurrency, distributed invariants, fencing, security "
    "boundaries, complex architecture, conflicting modules, or edit-dependent verification, stop "
    "analysis and return ESCALATE_TO_PRO with the complete Evidence Packet; do not solve it in Flash. "
    "Otherwise stop when the bounded root cause is supported and material alternatives are eliminated."
)

BLOCKED_CONTRACT = (
    "If blocked, return BLOCKED with what is missing, why it matters, and the minimum next step."
)


class RouteContractError(ValueError):
    """An agent/policy pair is outside the supported contract matrix."""


@dataclass(frozen=True)
class ReasoningContext:
    policy: str
    policy_contract: str
    model_tuning: str
    stop_condition: str

    def render(self, *, include_model_tuning: bool = True, fallback: bool = False) -> str:
        sections = [
            "POLICY\n" + self.policy,
            "POLICY EXECUTION CONTRACT\n" + self.policy_contract,
        ]
        if include_model_tuning and self.model_tuning:
            sections.append("MODEL-SPECIFIC TUNING\n" + self.model_tuning)
        sections.append("CONVERGENCE / STOP CONDITION\n" + self.stop_condition)
        if fallback:
            sections.append(
                "CAPABILITY BOUNDARY\nThis is an explicit text-only fallback request. It does not "
                "provide the native Codex subagent tool environment; use only supplied context and "
                "do not claim unprovided tool access, workspace edits, commands, or tests."
            )
        return "\n\n".join(sections)


def validate_route_contract(agent_type: str, policy: str) -> None:
    if agent_type not in VALID_ROUTE_MATRIX:
        raise RouteContractError(f"Unknown DeepSeek agent type: {agent_type!r}.")
    if policy not in POLICIES:
        raise RouteContractError(f"Unknown reasoning policy: {policy!r}.")
    if policy not in VALID_ROUTE_MATRIX[agent_type]:
        if agent_type == FLASH_AGENT and policy == "DEEP":
            raise RouteContractError(
                "DEEP policy requires deepseek_pro; deepseek_flash cannot accept DEEP."
            )
        raise RouteContractError(f"{agent_type} cannot accept the {policy} policy.")


def get_policy_contract(agent_type: str, policy: str) -> str:
    validate_route_contract(agent_type, policy)
    if policy == "FAST":
        return FAST_CONTRACT
    if policy == "REACT":
        return FLASH_REACT_CONTRACT if agent_type == FLASH_AGENT else PRO_REACT_CONTRACT
    if policy == "SPEC":
        return FLASH_SPEC_CONTRACT if agent_type == FLASH_AGENT else PRO_SPEC_CONTRACT
    return DEEP_CONTRACT


def get_model_tuning(agent_type: str, policy: str) -> str:
    validate_route_contract(agent_type, policy)
    return FLASH_TUNING if agent_type == FLASH_AGENT else PRO_TUNING_MINIMAL


def get_stop_condition(agent_type: str, policy: str) -> str:
    validate_route_contract(agent_type, policy)
    stop = FLASH_SPEC_STOP if agent_type == FLASH_AGENT and policy == "SPEC" else STOP_CONDITIONS[policy]
    return stop + " " + BLOCKED_CONTRACT


def build_reasoning_context(agent_type: str, policy: str) -> ReasoningContext:
    validate_route_contract(agent_type, policy)
    return ReasoningContext(
        policy=policy,
        policy_contract=get_policy_contract(agent_type, policy),
        model_tuning=get_model_tuning(agent_type, policy),
        stop_condition=get_stop_condition(agent_type, policy),
    )


def render_reasoning_context(
    agent_type: str,
    policy: str,
    *,
    include_model_tuning: bool = True,
    fallback: bool = False,
) -> str:
    return build_reasoning_context(agent_type, policy).render(
        include_model_tuning=include_model_tuning,
        fallback=fallback,
    )


def _main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Render a DeepSeek reasoning contract.")
    parser.add_argument("--agent-type", choices=sorted(VALID_ROUTE_MATRIX), required=True)
    parser.add_argument("--policy", choices=sorted(POLICIES), required=True)
    parser.add_argument("--without-model-tuning", action="store_true")
    parser.add_argument("--fallback", action="store_true")
    args = parser.parse_args(argv)
    print(
        render_reasoning_context(
            args.agent_type,
            args.policy,
            include_model_tuning=not args.without_model_tuning,
            fallback=args.fallback,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
