from __future__ import annotations

from typing import Any

from .models import AgentEntry


def requires_approval(
    agent: AgentEntry,
    skill_id: str,
    *,
    agent_card: dict[str, Any] | None = None,
) -> bool:
    """承認要否を判定。優先順位:
    1. AgentRegistry agents[].approval.skills[skill].requires_approval (明示)
    2. AgentCard skills[].metadata.x-orchestrator.requires_approval (リモート申告)
    3. AgentRegistry agents[].approval.default
    4. グローバル既定 not_required

    安全側ルール: AgentCard が True を申告したら registry False でダウングレードしない。
    """
    skill_policy = agent.approval.skills.get(skill_id)
    explicit = skill_policy.requires_approval if skill_policy else None

    card_claim: bool | None = None
    if agent_card is not None:
        for s in agent_card.get("skills", []) or []:
            if s.get("id") == skill_id:
                meta = (s.get("metadata") or {}).get("x-orchestrator") or {}
                v = meta.get("requires_approval")
                if isinstance(v, bool):
                    card_claim = v
                break

    if explicit is None:
        if card_claim is not None:
            return card_claim
        return agent.approval.default == "required"

    # 明示があっても、Card 側が True (より厳しい) ならそちらを採用
    if card_claim is True:
        return True
    return explicit
