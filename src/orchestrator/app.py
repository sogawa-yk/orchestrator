"""Chainlit エントリ。

`chainlit run src/orchestrator/app.py -h --port 8000` で起動する。
"""
from __future__ import annotations

import logging
import uuid

import chainlit as cl
import structlog
from agents import Runner
from opentelemetry import trace

from orchestrator.agent import build_agent, build_context
from orchestrator.config import get_settings
from orchestrator.observability import (
    build_langfuse_openai_client,  # 副作用なし、shadow 用に import 可
    get_langfuse_client,
    init_otel,
    setup_logging,
)
from orchestrator.observability import metrics as _metrics

logger = structlog.get_logger("orchestrator.app")
tracer = trace.get_tracer("orchestrator.app")


@cl.on_chat_start
async def on_chat_start() -> None:
    settings = get_settings()
    setup_logging(settings.orch_log_level)
    init_otel(settings)
    get_langfuse_client(settings)

    try:
        ctx = build_context(settings)
    except FileNotFoundError as e:
        logger.error("registry_load_failed", error=str(e))
        await cl.Message(
            content=f"⚠️ AgentRegistry が読み込めませんでした: `{e}`\n\nConfigMap (`orchestrator-agents`) を確認してください。",
        ).send()
        return

    session_id = str(uuid.uuid4())
    ctx.session_id = session_id

    # セッションを覆う root span (このセッション中の全 chat.message がこの下に並ぶ)
    session_span = tracer.start_span(
        "chat.session",
        attributes={
            "chainlit.session_id": session_id,
            "chainlit.thread_id": cl.user_session.get("id") or "",
        },
    )

    agent = build_agent(ctx)

    cl.user_session.set("orch_context", ctx)
    cl.user_session.set("orch_agent", agent)
    cl.user_session.set("orch_input_history", [])
    cl.user_session.set("orch_session_span", session_span)
    cl.user_session.set("orch_session_id", session_id)

    _metrics.record_session_delta(+1)
    logger.info(
        "chat_session_started",
        session_id=session_id,
        enabled_agents=[a.id for a in ctx.registry.enabled_agents()],
    )

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


@cl.on_chat_end
async def on_chat_end() -> None:
    span = cl.user_session.get("orch_session_span")
    if span is not None:
        try:
            span.end()
        except Exception:  # noqa: BLE001
            pass
    _metrics.record_session_delta(-1)
    sid = cl.user_session.get("orch_session_id")
    logger.info("chat_session_ended", session_id=sid)


@cl.on_message
async def on_message(msg: cl.Message) -> None:
    agent = cl.user_session.get("orch_agent")
    ctx = cl.user_session.get("orch_context")
    history: list = cl.user_session.get("orch_input_history") or []
    if agent is None or ctx is None:
        await cl.Message(content="セッションが初期化されていません。再読み込みしてください。").send()
        return

    history.append({"role": "user", "content": msg.content})

    with tracer.start_as_current_span(
        "chat.message",
        attributes={
            "chainlit.session_id": ctx.session_id or "",
            "chat.user_message_chars": len(msg.content or ""),
        },
    ):
        try:
            result = await Runner.run(
                starting_agent=agent, input=history, context=ctx, max_turns=20
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("agent_run_failed", error=str(e), error_type=type(e).__name__)
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
