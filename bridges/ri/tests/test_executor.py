"""RiBridgeExecutor の単体テスト.

ri_v10 への httpx 呼び出しを respx でモックし、A2A v0.3 レスポンスから
v1.0 Message が正しく enqueue されることを確認する.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from bridge_ri.config import Settings
from bridge_ri.executor import RiBridgeExecutor


_UPSTREAM = "http://ri.example/a2a"


def _settings(token: str = "") -> Settings:
    return Settings(
        upstream_url=_UPSTREAM,
        bridge_auth_token=token,
        upstream_timeout_sec=5.0,
    )


def _fake_context(text: str, context_id: str = "ctx-123") -> Any:
    ctx = MagicMock()
    ctx.get_user_input.return_value = text
    ctx.context_id = context_id
    return ctx


@pytest.mark.asyncio
async def test_execute_happy_path() -> None:
    """ri_v10 が text part を返した場合に enqueue される."""
    executor = RiBridgeExecutor(_settings())
    queue = MagicMock()
    queue.enqueue_event = AsyncMock()
    body = {
        "jsonrpc": "2.0",
        "id": "x",
        "result": {
            "messageId": "msg-1",
            "contextId": "ctx-123",
            "role": "agent",
            "kind": "message",
            "parts": [
                {"kind": "text", "text": "Compartments: prod, dev, staging"},
            ],
            "metadata": {},
        },
    }
    with respx.mock:
        route = respx.post(_UPSTREAM).mock(
            return_value=httpx.Response(200, json=body)
        )
        await executor.execute(_fake_context("compartments を一覧"), queue)
    assert route.called
    call = queue.enqueue_event.await_args
    msg = call.args[0]
    # protobuf Message: parts[0].text にテキストが入っているはず
    assert any(
        getattr(p, "text", "").startswith("Compartments:")
        for p in getattr(msg, "parts", [])
    )


@pytest.mark.asyncio
async def test_execute_sends_v0_3_payload() -> None:
    """送信 body が v0.3 message/send 形式であることを確認."""
    executor = RiBridgeExecutor(_settings())
    queue = MagicMock()
    queue.enqueue_event = AsyncMock()
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "x",
                "result": {"parts": [{"kind": "text", "text": "ok"}], "kind": "message"},
            },
        )

    with respx.mock:
        respx.post(_UPSTREAM).mock(side_effect=_handler)
        await executor.execute(_fake_context("hello", context_id="ctx-zz"), queue)

    body = captured["body"]
    assert body["method"] == "message/send"
    assert body["params"]["message"]["role"] == "user"
    assert body["params"]["message"]["parts"][0] == {"kind": "text", "text": "hello"}
    assert body["params"]["message"]["contextId"] == "ctx-zz"


@pytest.mark.asyncio
async def test_execute_handles_jsonrpc_error() -> None:
    executor = RiBridgeExecutor(_settings())
    queue = MagicMock()
    queue.enqueue_event = AsyncMock()
    with respx.mock:
        respx.post(_UPSTREAM).mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "x",
                    "error": {"code": -32601, "message": "Method not found"},
                },
            )
        )
        await executor.execute(_fake_context("test"), queue)
    msg = queue.enqueue_event.await_args.args[0]
    text = "".join(getattr(p, "text", "") for p in getattr(msg, "parts", []))
    assert "JSON-RPC error" in text


@pytest.mark.asyncio
async def test_execute_handles_http_5xx() -> None:
    executor = RiBridgeExecutor(_settings())
    queue = MagicMock()
    queue.enqueue_event = AsyncMock()
    with respx.mock:
        respx.post(_UPSTREAM).mock(return_value=httpx.Response(503, text="busy"))
        await executor.execute(_fake_context("test"), queue)
    msg = queue.enqueue_event.await_args.args[0]
    text = "".join(getattr(p, "text", "") for p in getattr(msg, "parts", []))
    assert "503" in text


@pytest.mark.asyncio
async def test_execute_handles_timeout() -> None:
    executor = RiBridgeExecutor(_settings())
    queue = MagicMock()
    queue.enqueue_event = AsyncMock()
    with respx.mock:
        respx.post(_UPSTREAM).mock(side_effect=httpx.ReadTimeout("slow"))
        await executor.execute(_fake_context("test"), queue)
    msg = queue.enqueue_event.await_args.args[0]
    text = "".join(getattr(p, "text", "") for p in getattr(msg, "parts", []))
    assert "タイムアウト" in text


@pytest.mark.asyncio
async def test_execute_rejects_empty_input() -> None:
    executor = RiBridgeExecutor(_settings())
    queue = MagicMock()
    queue.enqueue_event = AsyncMock()
    await executor.execute(_fake_context("   "), queue)
    msg = queue.enqueue_event.await_args.args[0]
    text = "".join(getattr(p, "text", "") for p in getattr(msg, "parts", []))
    assert "空" in text


@pytest.mark.asyncio
async def test_execute_empty_response_returns_placeholder() -> None:
    executor = RiBridgeExecutor(_settings())
    queue = MagicMock()
    queue.enqueue_event = AsyncMock()
    with respx.mock:
        respx.post(_UPSTREAM).mock(
            return_value=httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": "x", "result": {"parts": []}},
            )
        )
        await executor.execute(_fake_context("test"), queue)
    msg = queue.enqueue_event.await_args.args[0]
    text = "".join(getattr(p, "text", "") for p in getattr(msg, "parts", []))
    assert "空応答" in text
