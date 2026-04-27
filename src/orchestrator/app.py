"""Chainlit エントリ。

`chainlit run src/orchestrator/app.py -h --port 8000` で起動する。
"""
from __future__ import annotations

import logging
import uuid

import chainlit as cl
from agents import Runner

from orchestrator.agent import build_agent, build_context
from orchestrator.config import get_settings
from orchestrator.observability import get_langfuse_client, init_otel

logger = logging.getLogger("orchestrator")


@cl.on_chat_start
async def on_chat_start() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.orch_log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_otel(settings)
    get_langfuse_client(settings)  # 初回 LLM 呼出より前に作っておく

    try:
        ctx = build_context(settings)
    except FileNotFoundError as e:
        logger.error("AgentRegistry 読込失敗: %s", e)
        await cl.Message(
            content=f"⚠️ AgentRegistry が読み込めませんでした: `{e}`\n\nConfigMap (`orchestrator-agents`) を確認してください。",
        ).send()
        return

    ctx.session_id = str(uuid.uuid4())
    agent = build_agent(ctx)

    cl.user_session.set("orch_context", ctx)
    cl.user_session.set("orch_agent", agent)
    cl.user_session.set("orch_input_history", [])

    enabled = ctx.registry.enabled_agents()
    if enabled:
        body = "現在利用可能なリモートエージェント:\n" + "\n".join(
            f"- **{a.display_name}** (`{a.id}`)" for a in enabled
        )
    else:
        body = "現在利用可能なリモートエージェントはありません。"
    await cl.Message(
        content=f"こんにちは。Orchestrator です。\n\n{body}\n\nご依頼内容を日本語でどうぞ。",
    ).send()


@cl.on_message
async def on_message(msg: cl.Message) -> None:
    agent = cl.user_session.get("orch_agent")
    ctx = cl.user_session.get("orch_context")
    history: list = cl.user_session.get("orch_input_history") or []
    if agent is None or ctx is None:
        await cl.Message(content="セッションが初期化されていません。再読み込みしてください。").send()
        return

    history.append({"role": "user", "content": msg.content})

    try:
        result = await Runner.run(starting_agent=agent, input=history, context=ctx, max_turns=20)
    except Exception as e:  # noqa: BLE001
        logger.exception("Agent.run failure")
        await cl.Message(content=f"エラー: `{type(e).__name__}: {e}`").send()
        return

    final_text = getattr(result, "final_output", None) or ""
    if not isinstance(final_text, str):
        final_text = str(final_text)

    new_input = result.to_input_list() if hasattr(result, "to_input_list") else None
    if isinstance(new_input, list):
        cl.user_session.set("orch_input_history", new_input)
    else:
        history.append({"role": "assistant", "content": final_text})
        cl.user_session.set("orch_input_history", history)

    await cl.Message(content=final_text or "(応答なし)").send()


# Chainlit 内蔵 FastAPI に /healthz を追加 (readiness/liveness 用)
try:
    from chainlit.server import app as _fastapi_app

    @_fastapi_app.get("/healthz")
    async def _healthz() -> dict[str, str]:
        return {"status": "ok"}
except Exception:  # noqa: BLE001
    # chainlit が未起動 (テスト import 時) は無視
    pass
