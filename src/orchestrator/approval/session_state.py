from __future__ import annotations

from typing import Literal

ApprovalDecision = Literal["approved", "rejected", "timeout"]


def record_approval(
    decisions: dict[tuple[str, str], str],
    agent_id: str,
    skill_id: str,
    decision: ApprovalDecision,
) -> None:
    decisions[(agent_id, skill_id)] = decision


def get_approval(
    decisions: dict[tuple[str, str], str],
    agent_id: str,
    skill_id: str,
) -> str | None:
    return decisions.get((agent_id, skill_id))


def clear_approval(
    decisions: dict[tuple[str, str], str],
    agent_id: str,
    skill_id: str,
) -> None:
    decisions.pop((agent_id, skill_id), None)
