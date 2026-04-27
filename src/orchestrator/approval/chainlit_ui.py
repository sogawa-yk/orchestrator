from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Chainlit はテスト時にインポートできない可能性があるため、関数の中で遅延 import する。


async def ask_action(
    *, agent_id: str, skill_id: str, payload: dict[str, Any], reason: str, timeout: int = 300
) -> dict[str, str]:
    """Chainlit AskActionMessage で承認 UI を出し、ユーザーの選択を返す。

    返り値: `{"decision": "approved" | "rejected" | "timeout", "reason": "..."}`
    """
    try:
        import chainlit as cl
    except ImportError:
        logger.warning("Chainlit が利用不能。stub として approved を返す。")
        return {"decision": "approved", "reason": "chainlit unavailable (stub)"}

    body = (
        f"**承認が必要です**\n\n"
        f"- agent: `{agent_id}`\n"
        f"- skill: `{skill_id}`\n"
        f"- 理由: {reason}\n"
        f"- payload:\n```json\n{payload}\n```"
    )
    actions = [
        cl.Action(name="approve", payload={"decision": "approved"}, label="承認"),
        cl.Action(name="reject", payload={"decision": "rejected"}, label="却下"),
    ]
    res = await cl.AskActionMessage(content=body, actions=actions, timeout=timeout).send()
    if res is None:
        return {"decision": "timeout", "reason": "ユーザー無応答"}
    decision = (res.get("payload") or {}).get("decision") or res.get("name") or "rejected"
    return {"decision": decision, "reason": ""}


async def ask_input(prompt: str, *, timeout: int = 300) -> str | None:
    """A2A `input-required` で追加情報をユーザーから取得する。"""
    try:
        import chainlit as cl
    except ImportError:
        logger.warning("Chainlit が利用不能。stub として None を返す。")
        return None

    msg = await cl.AskUserMessage(content=prompt, timeout=timeout).send()
    if msg is None:
        return None
    if isinstance(msg, dict):
        return msg.get("output") or msg.get("content")
    return getattr(msg, "content", None)
