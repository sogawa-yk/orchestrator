"""ri_v10 の 3 skill を A2A v1.0 AgentCard で再公開する.

ri_v10 (`/home/opc/Github/ri_v10/ri/server.py` L46-62) の skill 定義をコピー.
ri_v10 を触らない制約のため文字列はソース内に直書きする (運用変更時はここを更新).
"""
from __future__ import annotations

from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard, AgentSkill


_SKILLS: list[AgentSkill] = [
    AgentSkill(
        id="resource-search",
        name="Resource Search",
        description=(
            "リージョン、コンパートメントを横断して OCI リソースを検索・一覧化する (Read-only)"
        ),
        tags=["oci", "resource-discovery", "read"],
    ),
    AgentSkill(
        id="dependency-map",
        name="Dependency Map",
        description="リソース間の依存関係を解析し、構造化されたマップを生成する (Read-only)",
        tags=["oci", "resource-discovery", "read"],
    ),
    AgentSkill(
        id="resource-creator",
        name="Resource Creator Lookup",
        description="タグまたは監査ログから OCI リソースの作成者を特定する (Read-only)",
        tags=["oci", "audit", "read"],
    ),
]


def build_agent_card() -> AgentCard:
    return AgentCard(
        name="resource-intelligence",
        description=(
            "OCI テナント全体のリソース検索・一覧化・依存関係の特定・作成者特定を行う Read-only エージェント"
            " (a2a-sdk v1.0 への bridge 経由でアクセス)"
        ),
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=_SKILLS,
    )
