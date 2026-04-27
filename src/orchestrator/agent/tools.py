from __future__ import annotations

import logging
from typing import Any

from agents import RunContextWrapper, function_tool
from opentelemetry import trace

from ..a2a_client import (
    A2AClient,
    InputRequired,
    RemoteAgentFailed,
    RemoteAgentTimeout,
    RemoteAgentUnauthorized,
    RemoteAgentUnavailable,
    resolve_bearer_token,
)
from ..approval import chainlit_ui, session_state
from ..observability import metrics as _metrics
from ..registry.policy import requires_approval
from .context import OrchestratorContext

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


def _ctx(ctx: RunContextWrapper["OrchestratorContext"]) -> "OrchestratorContext":
    if ctx.context is None:
        raise RuntimeError("OrchestratorContext is missing")
    return ctx.context


@function_tool
async def list_remote_agents(
    ctx: RunContextWrapper["OrchestratorContext"],
) -> list[dict[str, Any]]:
    """利用可能なリモートエージェントの一覧を返す。"""
    c = _ctx(ctx)
    with _metrics.measure_tool_latency("list_remote_agents"):
        out: list[dict[str, Any]] = []
        for a in c.registry.enabled_agents():
            out.append(
                {
                    "agent_id": a.id,
                    "display_name": a.display_name,
                    "tags": list(a.tags),
                    "enabled": a.enabled,
                    "notes": a.notes,
                }
            )
        return out


@function_tool
async def describe_remote_agent(
    ctx: RunContextWrapper["OrchestratorContext"],
    agent_id: str,
) -> dict[str, Any]:
    """指定エージェントの詳細 (skills と承認要否ヒント) を返す。"""
    c = _ctx(ctx)
    with _metrics.measure_tool_latency("describe_remote_agent"):
        agent = c.registry.get(agent_id)
        if agent is None or not agent.enabled:
            return {
                "error": f"agent_id={agent_id} は利用不能",
                "available_agents": [a.id for a in c.registry.enabled_agents()],
            }
        token = resolve_bearer_token(agent)
        try:
            card = await c.card_cache.get(agent.id, agent.base_url, token)
        except Exception as e:  # noqa: BLE001
            logger.warning("AgentCard 取得失敗 agent=%s: %s", agent.id, e)
            return {"error": f"AgentCard 取得失敗: {e}"}
        skills_out: list[dict[str, Any]] = []
        for s in card.get("skills") or []:
            sid = s.get("id")
            if not sid:
                continue
            skills_out.append(
                {
                    "id": sid,
                    "name": s.get("name"),
                    "description": s.get("description"),
                    "tags": s.get("tags") or [],
                    "needs_approval": requires_approval(agent, sid, agent_card=card),
                }
            )
        return {
            "agent_id": agent.id,
            "display_name": agent.display_name,
            "version": card.get("version"),
            "description": card.get("description"),
            "capabilities": card.get("capabilities") or {},
            "skills": skills_out,
        }


@function_tool(strict_mode=False)
async def request_user_approval(
    ctx: RunContextWrapper["OrchestratorContext"],
    agent_id: str,
    skill_id: str,
    payload: dict[str, Any],
    reason: str,
) -> dict[str, str]:
    """ユーザーに承認 UI を提示し、選択結果を返す。"""
    c = _ctx(ctx)
    with tracer.start_as_current_span(
        "tool.request_user_approval",
        attributes={
            "agent.id": agent_id,
            "skill.id": skill_id,
            "approval.reason": reason,
        },
    ) as span, _metrics.measure_tool_latency("request_user_approval"):
        result = await chainlit_ui.ask_action(
            agent_id=agent_id, skill_id=skill_id, payload=payload, reason=reason
        )
        decision = result.get("decision", "rejected")
        span.set_attribute("approval.decision", decision)
        if decision in ("approved", "rejected", "timeout"):
            session_state.record_approval(
                c.approval_decisions, agent_id, skill_id, decision  # type: ignore[arg-type]
            )
        _metrics.record_approval(agent_id, skill_id, decision)
        return result


@function_tool
async def call_remote_agent(
    ctx: RunContextWrapper["OrchestratorContext"],
    agent_id: str,
    skill_id: str,
    message: str,
    context_id: str | None = None,
) -> dict[str, Any]:
    """リモートエージェントを A2A で呼び出す。承認チェックと input-required の自動再開を含む。"""
    c = _ctx(ctx)
    agent = c.registry.get(agent_id)
    if agent is None or not agent.enabled:
        return {"error": f"agent_id={agent_id} は利用不能", "denied": False}

    # AgentCard 取得 (policy 判定で使う)
    token = resolve_bearer_token(agent)
    card: dict[str, Any] | None = None
    try:
        card = await c.card_cache.get(agent.id, agent.base_url, token)
    except Exception as e:  # noqa: BLE001
        logger.warning("AgentCard 取得失敗 agent=%s: %s", agent.id, e)

    # 承認ポリシー判定
    needs_approval = requires_approval(agent, skill_id, agent_card=card)
    if needs_approval:
        decision = session_state.get_approval(
            c.approval_decisions, agent_id, skill_id
        )
        if decision != "approved":
            _metrics.record_agent_call(agent_id, skill_id, "denied")
            return {
                "error": "承認が必要なスキルです。先に request_user_approval を呼んでください。",
                "needs_approval": True,
                "denied": True,
            }

    # context_id 解決 (Agent から渡されなければ前回値を再利用)
    cid = context_id or c.context_ids.get(agent_id)

    span_attrs = {
        "agent.id": agent_id,
        "skill.id": skill_id,
        "tool": "call_remote_agent",
    }
    if cid:
        span_attrs["context.id"] = cid

    with tracer.start_as_current_span("tool.call_remote_agent", attributes=span_attrs) as span, _metrics.measure_tool_latency("call_remote_agent"):
        timeout_s = c.registry.defaults.timeout_seconds
        outcome = "failed"
        try:
            async with A2AClient(
                agent,
                token,
                timeout_seconds=timeout_s,
                retry=c.registry.defaults.retry,
            ) as client:
                try:
                    res = await client.send_message(
                        message, context_id=cid, skill_hint=skill_id
                    )
                except InputRequired as e:
                    span.set_attribute("a2a.state", "input-required")
                    user_text = await chainlit_ui.ask_input(e.prompt)
                    if not user_text:
                        outcome = "input_required_canceled"
                        _metrics.record_agent_call(agent_id, skill_id, outcome)
                        return {
                            "error": "input-required で無応答のため中止",
                            "state": "canceled",
                            "task_id": e.task_id,
                            "context_id": e.context_id,
                        }
                    res = await client.resume_with_user_input(
                        user_text,
                        task_id=e.task_id or "",
                        context_id=e.context_id,
                    )
                    outcome = "input_required_resumed"
                else:
                    outcome = "success"
        except RemoteAgentUnauthorized as e:
            _metrics.record_agent_call(agent_id, skill_id, "unauthorized")
            return {"error": f"unauthorized: {e}", "kind": "unauthorized"}
        except RemoteAgentUnavailable as e:
            _metrics.record_agent_call(agent_id, skill_id, "unavailable")
            return {"error": f"unavailable: {e}", "kind": "unavailable"}
        except RemoteAgentTimeout as e:
            _metrics.record_agent_call(agent_id, skill_id, "timeout")
            return {"error": f"timeout: {e}", "kind": "timeout"}
        except RemoteAgentFailed as e:
            _metrics.record_agent_call(agent_id, skill_id, "failed")
            return {"error": f"failed: {e}", "kind": "failed"}

        if res.context_id:
            c.context_ids[agent_id] = res.context_id

        span.set_attribute("a2a.state", res.state)
        if res.task_id:
            span.set_attribute("a2a.task.id", res.task_id)
        _metrics.record_agent_call(agent_id, skill_id, outcome)

        return {
            "final_text": res.final_text,
            "state": res.state,
            "task_id": res.task_id,
            "context_id": res.context_id,
        }


ALL_TOOLS = (
    list_remote_agents,
    describe_remote_agent,
    request_user_approval,
    call_remote_agent,
)
