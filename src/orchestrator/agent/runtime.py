from __future__ import annotations

import logging
from pathlib import Path

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel

from ..config import Settings, get_settings
from ..observability.langfuse_setup import build_langfuse_openai_client
from ..registry import Registry, load_registry
from ..registry.card_cache import AgentCardCache
from .context import OrchestratorContext
from .tools import ALL_TOOLS

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_system_prompt() -> str:
    return (_PROMPTS_DIR / "system.ja.md").read_text(encoding="utf-8")


def _build_available_agents_block(registry: Registry) -> str:
    enabled = registry.enabled_agents()
    if not enabled:
        return "<available_agents>\n(現在利用可能なリモートエージェントはありません)\n</available_agents>"
    lines = ["<available_agents>"]
    for a in enabled:
        tags = ", ".join(a.tags) if a.tags else "-"
        lines.append(f"- id: {a.id}")
        lines.append(f"  display_name: {a.display_name}")
        lines.append(f"  tags: {tags}")
    lines.append("</available_agents>")
    return "\n".join(lines)


def build_context(settings: Settings | None = None) -> OrchestratorContext:
    s = settings or get_settings()
    json_text = s.a2a_agents_json or None
    if json_text and json_text.strip() and s.orch_agents_path:
        logger.warning(
            "A2A_AGENTS_JSON が設定されているため ORCH_AGENTS_PATH=%s は無視されます",
            s.orch_agents_path,
        )
    registry = load_registry(s.orch_agents_path, json_text=json_text)
    cache = AgentCardCache(ttl_seconds=registry.defaults.card_cache_ttl_seconds)
    return OrchestratorContext(settings=s, registry=registry, card_cache=cache)


def build_agent(context: OrchestratorContext) -> Agent[OrchestratorContext]:
    """ReAct エージェントを構築する (単一ループ)。"""
    s = context.settings
    openai_client = build_langfuse_openai_client(s)
    model = OpenAIChatCompletionsModel(model=s.orch_model, openai_client=openai_client)

    instructions = (
        _load_system_prompt()
        + "\n\n"
        + _build_available_agents_block(context.registry)
    )

    agent: Agent[OrchestratorContext] = Agent(
        name="orchestrator",
        instructions=instructions,
        tools=list(ALL_TOOLS),
        model=model,
        model_settings=ModelSettings(temperature=0.2),
    )
    return agent
