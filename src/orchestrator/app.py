"""Chainlit エントリ。

`chainlit run src/orchestrator/app.py -h --port 8000` で起動する。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import chainlit as cl
import structlog
from agents import Runner
from agents.stream_events import (
    RawResponsesStreamEvent,
    RunItemStreamEvent,
)
from opentelemetry import trace

from orchestrator.agent import build_agent, build_context
from orchestrator.config import get_settings
from orchestrator.observability import (
    get_langfuse_client,
    init_otel,
    setup_logging,
)
from orchestrator.observability import metrics as _metrics

# Langfuse trace 属性で固定的に使うユーザー識別子。
# Chainlit に認証を導入した時点で `cl.User.identifier` などへ差し替える。
_ANONYMOUS_USER_ID = "anonymous"

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

    # セッションを覆う root span (このセッション中の全 chat.message がこの下に並ぶ)。
    # langfuse.session.id / langfuse.user.id / langfuse.tags は Langfuse が
    # Trace 属性として読み取り Sessions / Users / Tag フィルタの集約に使う。
    session_span = tracer.start_span(
        "chat.session",
        attributes={
            "chainlit.session_id": session_id,
            "chainlit.thread_id": cl.user_session.get("id") or "",
            "langfuse.session.id": session_id,
            "langfuse.user.id": _ANONYMOUS_USER_ID,
            "langfuse.tags": ("chainlit", "orchestrator", settings.orch_environment),
            "langfuse.environment": settings.orch_environment,
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
            "langfuse.session.id": ctx.session_id or "",
            "langfuse.user.id": _ANONYMOUS_USER_ID,
            # Langfuse Trace ビューの「Input」欄に表示される。
            "input.value": msg.content or "",
        },
    ) as span:
        final_msg = cl.Message(content="")
        await final_msg.send()
        # call_id -> (Step, tool_name) を保持して output 時に紐付ける。
        open_steps: dict[str, tuple[cl.Step, str]] = {}

        try:
            streamed = Runner.run_streamed(
                starting_agent=agent, input=history, context=ctx, max_turns=20
            )
            async for ev in streamed.stream_events():
                if isinstance(ev, RawResponsesStreamEvent):
                    data = ev.data
                    # Chat Completions / Responses 共通で出るテキスト delta。
                    if getattr(data, "type", None) == "response.output_text.delta":
                        delta_text = getattr(data, "delta", "") or ""
                        if delta_text:
                            await final_msg.stream_token(delta_text)
                elif isinstance(ev, RunItemStreamEvent):
                    if ev.name == "tool_called":
                        await _open_tool_step(ev.item, open_steps)
                    elif ev.name == "tool_output":
                        await _close_tool_step(ev.item, open_steps)
                # その他 (handoff / reasoning / message_output_created) は無視
        except Exception as e:  # noqa: BLE001
            logger.exception("agent_run_failed", error=str(e), error_type=type(e).__name__)
            span.set_attribute("output.value", f"ERROR: {type(e).__name__}: {e}")
            for step, _ in open_steps.values():
                step.output = f"中断: {type(e).__name__}"
                await step.update()
            await cl.Message(content=f"エラー: `{type(e).__name__}: {e}`").send()
            return

        final_output = streamed.final_output
        if final_output is None:
            final_text = ""
        elif isinstance(final_output, str):
            final_text = final_output
        else:
            final_text = str(final_output)

        # トークンストリーミングで埋まらなかったケース (ツールのみで完了等) に備える。
        if not final_msg.content and final_text:
            final_msg.content = final_text
        await final_msg.update()

        # Langfuse Trace ビューの「Output」欄に表示される。
        span.set_attribute("output.value", final_text or "")

        new_input = streamed.to_input_list() if hasattr(streamed, "to_input_list") else None
        if isinstance(new_input, list):
            cl.user_session.set("orch_input_history", new_input)
        else:
            history.append({"role": "assistant", "content": final_text})
            cl.user_session.set("orch_input_history", history)


def _raw_call_id(raw_item: Any) -> str | None:
    """ToolCallItem / ToolCallOutputItem の raw_item から call_id を取り出す。"""
    cid = getattr(raw_item, "call_id", None)
    if cid:
        return str(cid)
    if isinstance(raw_item, dict):
        v = raw_item.get("call_id")
        return str(v) if v else None
    return None


def _raw_tool_name(raw_item: Any) -> str:
    n = getattr(raw_item, "name", None)
    if n:
        return str(n)
    if isinstance(raw_item, dict):
        return str(raw_item.get("name") or "")
    return ""


def _raw_arguments(raw_item: Any) -> dict[str, Any]:
    """raw_item.arguments は JSON 文字列。dict に戻す。失敗時は空 dict。"""
    args = getattr(raw_item, "arguments", None)
    if args is None and isinstance(raw_item, dict):
        args = raw_item.get("arguments")
    if isinstance(args, dict):
        return args
    if isinstance(args, str) and args.strip():
        try:
            v = json.loads(args)
            return v if isinstance(v, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _summarize_tool_call(tool_name: str, args: dict[str, Any]) -> str:
    """ツール呼び出しの 1 行サマリ (Step 名に使う)。"""
    if tool_name == "list_remote_agents":
        return "利用可能なエージェント一覧を取得"
    if tool_name == "describe_remote_agent":
        return f"エージェント `{args.get('agent_id', '?')}` の詳細を取得"
    if tool_name == "request_user_approval":
        reason = str(args.get("reason") or "").strip()
        if len(reason) > 60:
            reason = reason[:57] + "…"
        return f"ユーザー承認をリクエスト: {reason}" if reason else "ユーザー承認をリクエスト"
    if tool_name == "call_remote_agent":
        return f"`{args.get('agent_id', '?')}` に `{args.get('skill_id', '?')}` を依頼"
    return f"ツール `{tool_name}` を実行" if tool_name else "ツール実行"


_A2A_STATE_JA = {
    "completed": "完了",
    "failed": "失敗",
    "canceled": "中止",
    "input-required": "追加入力待ち",
    "working": "実行中",
    "submitted": "送信済み",
}

_DECISION_JA = {
    "approved": "承認",
    "rejected": "拒否",
    "timeout": "タイムアウト",
}


def _summarize_tool_output(tool_name: str, output: Any) -> str:
    """ツール出力の 1 行サマリ。output は ToolCallOutputItem.output (dict / list / str)。"""
    if tool_name == "list_remote_agents":
        if isinstance(output, list):
            return f"{len(output)} 件取得"
        return "取得"
    if tool_name == "describe_remote_agent":
        if isinstance(output, dict):
            if output.get("error"):
                return "失敗"
            skills = output.get("skills")
            n = len(skills) if isinstance(skills, list) else 0
            return f"{n} skill"
        return "取得"
    if tool_name == "request_user_approval":
        if isinstance(output, dict):
            return _DECISION_JA.get(str(output.get("decision") or ""), str(output.get("decision") or "不明"))
        return "完了"
    if tool_name == "call_remote_agent":
        if isinstance(output, dict):
            if output.get("error"):
                kind = output.get("kind") or ("denied" if output.get("denied") else "失敗")
                return f"失敗 ({kind})"
            state = str(output.get("state") or "")
            return _A2A_STATE_JA.get(state, state or "完了")
        return "完了"
    return "完了"


async def _open_tool_step(
    item: Any, open_steps: dict[str, tuple[cl.Step, str]]
) -> None:
    raw = getattr(item, "raw_item", None)
    name = _raw_tool_name(raw)
    args = _raw_arguments(raw)
    call_id = _raw_call_id(raw) or str(uuid.uuid4())
    step = cl.Step(
        name=_summarize_tool_call(name, args),
        type="tool",
        show_input=False,
    )
    await step.send()
    open_steps[call_id] = (step, name)


async def _close_tool_step(
    item: Any, open_steps: dict[str, tuple[cl.Step, str]]
) -> None:
    raw = getattr(item, "raw_item", None)
    call_id = _raw_call_id(raw)
    entry = open_steps.pop(call_id, None) if call_id else None
    if entry is None:
        return
    step, tool_name = entry
    # ToolCallOutputItem は output 属性に生の戻り値 (dict 等) を持つ。
    output_value = getattr(item, "output", None)
    step.output = _summarize_tool_output(tool_name, output_value)
    await step.update()


# Chainlit 内蔵 FastAPI に /healthz を追加 (readiness/liveness 用)
try:
    from chainlit.server import app as _fastapi_app

    @_fastapi_app.get("/healthz")
    async def _healthz() -> dict[str, str]:
        return {"status": "ok"}
except Exception:  # noqa: BLE001
    # chainlit が未起動 (テスト import 時) は無視
    pass
