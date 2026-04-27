from __future__ import annotations

import textwrap
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from agents import RunContextWrapper
from agents.tool_context import ToolContext

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
