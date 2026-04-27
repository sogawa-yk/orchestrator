from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Settings
from ..registry import Registry
from ..registry.card_cache import AgentCardCache


@dataclass
class OrchestratorContext:
    """Agent ツールに渡される実行時コンテキスト。

    - registry: ConfigMap から読み込んだ AgentRegistry
    - card_cache: AgentCard の TTL キャッシュ
    - approval_decisions: 承認結果 (agent_id, skill_id) -> "approved"/"rejected"/"timeout"
    - context_ids: agent_id ごとに最後に取得した A2A context_id を保持
    """

    settings: Settings
    registry: Registry
    card_cache: AgentCardCache
    approval_decisions: dict[tuple[str, str], str] = field(default_factory=dict)
    context_ids: dict[str, str] = field(default_factory=dict)
    session_id: str | None = None
