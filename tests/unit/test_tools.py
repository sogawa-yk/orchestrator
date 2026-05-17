from __future__ import annotations

import textwrap
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from agents import RunContextWrapper
from agents.tool_context import ToolContext
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from orchestrator.agent.context import OrchestratorContext
from orchestrator.agent.tools import (
    call_remote_agent,
    describe_remote_agent,
    list_remote_agents,
    request_user_approval,
)
from orchestrator.config import Settings
from orchestrator.registry.card_cache import AgentCardCache
from orchestrator.registry.loader import load_registry_from_text


REG_YAML = textwrap.dedent(
    """\
    version: 1
    defaults:
      timeout_seconds: 30
      retry: { max_attempts: 1, backoff_seconds: 0.01 }
      card_cache_ttl_seconds: 60
    agents:
      - id: ta
        display_name: TA
        base_url: http://ta.example/a2a
        auth: { kind: bearer, token_env: TA_TOKEN }
        enabled: true
        tags: [obs]
        approval:
          default: not_required
          skills:
            diagnose: { requires_approval: false }
            danger: { requires_approval: true }
    """
)

CARD_JSON = {
    "name": "ta",
    "description": "観測診断エージェント",
    "version": "0.1.0",
    "capabilities": {"streaming": False},
    "skills": [
        {"id": "diagnose", "name": "障害診断", "description": "...", "tags": ["obs"]},
        {"id": "danger", "name": "危険", "description": "...", "tags": []},
    ],
}


@pytest.fixture()
def ctx(monkeypatch) -> OrchestratorContext:
    monkeypatch.setenv("TA_TOKEN", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    settings = Settings()
    registry = load_registry_from_text(REG_YAML)
    return OrchestratorContext(
        settings=settings, registry=registry, card_cache=AgentCardCache(ttl_seconds=60)
    )


import json as _json


async def _call_tool(tool, ctx: OrchestratorContext, **args: Any):
    """function_tool 化された tool を ToolContext 経由で直接呼ぶテストヘルパ。

    Runner 経由ではなく on_invoke_tool に文字列 JSON を渡す。返値は `tool.on_invoke_tool`
    が返した文字列 (JSON) のため、本ライブラリでは Python オブジェクトを JSON シリアライズして
    返す挙動。dict/list の場合は parse して返す。
    """
    tc = ToolContext(
        context=ctx,
        tool_name=tool.name,
        tool_call_id="test-call-1",
        tool_arguments=_json.dumps(args),
    )
    out = await tool.on_invoke_tool(tc, _json.dumps(args))
    if isinstance(out, str):
        try:
            return _json.loads(out)
        except _json.JSONDecodeError:
            return out
    return out


async def test_list_remote_agents(ctx) -> None:
    out = await _call_tool(list_remote_agents, ctx)
    assert isinstance(out, list)
    assert out[0]["agent_id"] == "ta"
    assert out[0]["enabled"] is True


@respx.mock
async def test_describe_remote_agent_success(ctx) -> None:
    respx.get("http://ta.example/a2a/.well-known/agent-card.json").mock(
        return_value=httpx.Response(200, json=CARD_JSON)
    )
    out = await _call_tool(describe_remote_agent, ctx, agent_id="ta")
    assert out["agent_id"] == "ta"
    skills = {s["id"]: s for s in out["skills"]}
    assert skills["diagnose"]["needs_approval"] is False
    assert skills["danger"]["needs_approval"] is True


async def test_describe_unknown_agent(ctx) -> None:
    out = await _call_tool(describe_remote_agent, ctx, agent_id="missing")
    assert "error" in out


@respx.mock
async def test_call_remote_agent_blocked_without_approval(ctx) -> None:
    respx.get("http://ta.example/a2a/.well-known/agent-card.json").mock(
        return_value=httpx.Response(200, json=CARD_JSON)
    )
    out = await _call_tool(
        call_remote_agent,
        ctx,
        agent_id="ta",
        skill_id="danger",
        message="x",
    )
    assert out.get("needs_approval") is True
    assert out.get("denied") is True


@respx.mock
async def test_call_remote_agent_success(ctx) -> None:
    respx.get("http://ta.example/a2a/.well-known/agent-card.json").mock(
        return_value=httpx.Response(200, json=CARD_JSON)
    )
    respx.post("http://ta.example/a2a/").mock(
        return_value=httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "x",
                "result": {
                    "task": {
                        "id": "t1",
                        "contextId": "c1",
                        "status": {"state": "TASK_STATE_COMPLETED"},
                        "artifacts": [{"parts": [{"text": "ok"}]}],
                    }
                },
            },
        )
    )
    out = await _call_tool(
        call_remote_agent,
        ctx,
        agent_id="ta",
        skill_id="diagnose",
        message="x",
    )
    assert out["state"] == "completed"
    assert out["final_text"] == "ok"
    assert ctx.context_ids["ta"] == "c1"


@respx.mock
async def test_request_user_approval_records_decision(ctx) -> None:
    with patch("orchestrator.agent.tools.chainlit_ui.ask_action", new=AsyncMock(return_value={"decision": "approved", "reason": ""})):
        out = await _call_tool(
            request_user_approval,
            ctx,
            agent_id="ta",
            skill_id="danger",
            payload={"x": 1},
            reason="test",
        )
    assert out["decision"] == "approved"
    assert ctx.approval_decisions[("ta", "danger")] == "approved"


@pytest.fixture()
def span_exporter() -> InMemorySpanExporter:
    """tools.py の tracer を InMemorySpanExporter に差し替えるフィクスチャ。

    `tools.py` の `tracer` は import 時に `trace.get_tracer(__name__)` で取得され、
    遅延バインドで現在の TracerProvider を解決するため、ここで provider を入れ替える
    だけで span が拾える。
    """
    import orchestrator.agent.tools as tools_mod

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    original_provider = trace._TRACER_PROVIDER
    trace._TRACER_PROVIDER = provider
    # tools_mod.tracer は ProxyTracer のため自動で新 provider を見るが、念のため再取得。
    original_tracer = tools_mod.tracer
    tools_mod.tracer = provider.get_tracer("orchestrator.agent.tools")
    try:
        yield exporter
    finally:
        tools_mod.tracer = original_tracer
        trace._TRACER_PROVIDER = original_provider


def _find_span(exporter: InMemorySpanExporter, name: str):
    for s in exporter.get_finished_spans():
        if s.name == name:
            return s
    raise AssertionError(f"span {name!r} not found; got {[s.name for s in exporter.get_finished_spans()]}")


@respx.mock
async def test_call_remote_agent_span_attrs_success(ctx, span_exporter) -> None:
    respx.get("http://ta.example/a2a/.well-known/agent-card.json").mock(
        return_value=httpx.Response(200, json=CARD_JSON)
    )
    respx.post("http://ta.example/a2a/").mock(
        return_value=httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "x",
                "result": {
                    "task": {
                        "id": "t1",
                        "contextId": "c1",
                        "status": {"state": "TASK_STATE_COMPLETED"},
                        "artifacts": [{"parts": [{"text": "ok"}]}],
                    }
                },
            },
        )
    )
    await _call_tool(
        call_remote_agent, ctx, agent_id="ta", skill_id="diagnose", message="hi"
    )
    span = _find_span(span_exporter, "tool.call_remote_agent")
    attrs = dict(span.attributes)
    assert attrs["agent.id"] == "ta"
    assert attrs["skill.id"] == "diagnose"
    assert attrs["routing.reason"] == "from_registry_listed"
    assert attrs["approval.policy"] == "not_required"
    assert attrs["context.id.source"] == "none"
    assert attrs["context.id.reused"] is False
    assert attrs["a2a.outcome"] == "success"
    assert attrs["input.value"] == "hi"
    assert attrs["output.value"] == "ok"


@respx.mock
async def test_call_remote_agent_span_attrs_unknown_agent(ctx, span_exporter) -> None:
    await _call_tool(
        call_remote_agent, ctx, agent_id="missing", skill_id="x", message="m"
    )
    span = _find_span(span_exporter, "tool.call_remote_agent")
    attrs = dict(span.attributes)
    assert attrs["routing.reason"] == "unknown_agent"
    assert attrs["a2a.outcome"] == "rejected_unknown_agent"


@respx.mock
async def test_call_remote_agent_span_attrs_denied(ctx, span_exporter) -> None:
    respx.get("http://ta.example/a2a/.well-known/agent-card.json").mock(
        return_value=httpx.Response(200, json=CARD_JSON)
    )
    await _call_tool(
        call_remote_agent, ctx, agent_id="ta", skill_id="danger", message="m"
    )
    span = _find_span(span_exporter, "tool.call_remote_agent")
    attrs = dict(span.attributes)
    assert attrs["approval.policy"] == "required_denied"
    assert attrs["a2a.outcome"] == "denied_needs_approval"


@respx.mock
async def test_call_remote_agent_span_attrs_context_reuse(ctx, span_exporter) -> None:
    ctx.context_ids["ta"] = "prev-cid"
    respx.get("http://ta.example/a2a/.well-known/agent-card.json").mock(
        return_value=httpx.Response(200, json=CARD_JSON)
    )
    respx.post("http://ta.example/a2a/").mock(
        return_value=httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "x",
                "result": {
                    "task": {
                        "id": "t1",
                        "contextId": "prev-cid",
                        "status": {"state": "TASK_STATE_COMPLETED"},
                        "artifacts": [{"parts": [{"text": "ok"}]}],
                    }
                },
            },
        )
    )
    await _call_tool(
        call_remote_agent, ctx, agent_id="ta", skill_id="diagnose", message="follow-up"
    )
    span = _find_span(span_exporter, "tool.call_remote_agent")
    attrs = dict(span.attributes)
    assert attrs["context.id.source"] == "cached"
    assert attrs["context.id.reused"] is True
    assert attrs["context.id"] == "prev-cid"


async def test_request_user_approval_span_attrs(ctx, span_exporter) -> None:
    with patch(
        "orchestrator.agent.tools.chainlit_ui.ask_action",
        new=AsyncMock(return_value={"decision": "rejected", "reason": "user said no"}),
    ):
        await _call_tool(
            request_user_approval,
            ctx,
            agent_id="ta",
            skill_id="danger",
            payload={"k": "v"},
            reason="why-prompt",
        )
    span = _find_span(span_exporter, "tool.request_user_approval")
    attrs = dict(span.attributes)
    assert attrs["approval.reason"] == "why-prompt"
    assert attrs["approval.decision"] == "rejected"
    assert attrs["input.value"] == "why-prompt"
    assert attrs["output.value"] == "rejected"
    assert attrs["approval.payload_size"] > 0


@respx.mock
async def test_call_remote_agent_after_approval(ctx) -> None:
    ctx.approval_decisions[("ta", "danger")] = "approved"
    respx.get("http://ta.example/a2a/.well-known/agent-card.json").mock(
        return_value=httpx.Response(200, json=CARD_JSON)
    )
    respx.post("http://ta.example/a2a/").mock(
        return_value=httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "x",
                "result": {
                    "task": {
                        "id": "t2",
                        "contextId": "c2",
                        "status": {"state": "TASK_STATE_COMPLETED"},
                        "artifacts": [{"parts": [{"text": "done"}]}],
                    }
                },
            },
        )
    )
    out = await _call_tool(
        call_remote_agent,
        ctx,
        agent_id="ta",
        skill_id="danger",
        message="restart it",
    )
    assert out["state"] == "completed"
