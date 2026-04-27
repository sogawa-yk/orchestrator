from __future__ import annotations

import json

import httpx
import pytest
import respx

from orchestrator.a2a_client import (
    A2AClient,
    InputRequired,
    RemoteAgentFailed,
    RemoteAgentTimeout,
    RemoteAgentUnauthorized,
    RemoteAgentUnavailable,
)
from orchestrator.registry.models import AgentEntry, AuthSpec, RetrySpec


def _agent() -> AgentEntry:
    return AgentEntry(
        id="ta",
        display_name="TA",
        base_url="http://ta.example/a2a",
        auth=AuthSpec(kind="bearer", token_env="X"),
    )


@pytest.fixture()
def fast_retry() -> RetrySpec:
    return RetrySpec(max_attempts=2, backoff_seconds=0.01)


@respx.mock
async def test_send_message_completed_artifacts() -> None:
    rpc_response = {
        "jsonrpc": "2.0",
        "id": "x",
        "result": {
            "task": {
                "id": "task-1",
                "contextId": "ctx-1",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [{"parts": [{"text": "結果テキスト"}]}],
            }
        },
    }
    respx.post("http://ta.example/a2a/").mock(return_value=httpx.Response(200, json=rpc_response))
    async with A2AClient(_agent(), token="t") as c:
        res = await c.send_message("hello")
    assert res.state == "completed"
    assert res.task_id == "task-1"
    assert res.context_id == "ctx-1"
    assert "結果" in res.final_text


@respx.mock
async def test_send_message_message_only() -> None:
    rpc_response = {
        "jsonrpc": "2.0",
        "id": "x",
        "result": {
            "message": {
                "parts": [{"text": "agent reply"}],
                "contextId": "ctx-2",
            }
        },
    }
    respx.post("http://ta.example/a2a/").mock(return_value=httpx.Response(200, json=rpc_response))
    async with A2AClient(_agent(), token="t") as c:
        res = await c.send_message("hi")
    assert res.state == "completed"
    assert res.final_text == "agent reply"
    assert res.context_id == "ctx-2"


@respx.mock
async def test_input_required_raises() -> None:
    rpc_response = {
        "jsonrpc": "2.0",
        "id": "x",
        "result": {
            "task": {
                "id": "task-2",
                "contextId": "ctx-3",
                "status": {
                    "state": "TASK_STATE_INPUT_REQUIRED",
                    "message": {"parts": [{"text": "対象 namespace は？"}]},
                },
                "artifacts": [],
            }
        },
    }
    respx.post("http://ta.example/a2a/").mock(return_value=httpx.Response(200, json=rpc_response))
    async with A2AClient(_agent(), token="t") as c:
        with pytest.raises(InputRequired) as ei:
            await c.send_message("diagnose")
    assert ei.value.task_id == "task-2"
    assert ei.value.context_id == "ctx-3"
    assert "namespace" in ei.value.prompt


@respx.mock
async def test_request_uses_a2a_version_header_and_grpc_method() -> None:
    """SendMessage に A2A-Version: 1.0 ヘッダと SendMessage メソッド名を必ず使うこと"""
    captured: dict[str, object] = {}

    def _h(req: httpx.Request) -> httpx.Response:
        import json as _json

        captured["headers"] = dict(req.headers)
        captured["body"] = _json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "x",
                "result": {
                    "task": {
                        "id": "t",
                        "status": {"state": "TASK_STATE_COMPLETED"},
                        "artifacts": [{"parts": [{"text": "ok"}]}],
                    }
                },
            },
        )

    respx.post("http://ta.example/a2a/").mock(side_effect=_h)
    async with A2AClient(_agent(), token="t") as c:
        await c.send_message("x")
    assert captured["headers"]["a2a-version"] == "1.0"
    assert captured["body"]["method"] == "SendMessage"
    assert captured["body"]["params"]["message"]["role"] == "ROLE_USER"


@respx.mock
async def test_unauthorized() -> None:
    respx.post("http://ta.example/a2a/").mock(return_value=httpx.Response(401, text="bad token"))
    async with A2AClient(_agent(), token="t") as c:
        with pytest.raises(RemoteAgentUnauthorized):
            await c.send_message("x")


@respx.mock
async def test_unavailable_then_recover(fast_retry) -> None:
    rpc_response = {
        "jsonrpc": "2.0",
        "id": "x",
        "result": {
            "task": {
                "id": "task-3",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [{"parts": [{"text": "ok"}]}],
            }
        },
    }
    route = respx.post("http://ta.example/a2a/").mock(
        side_effect=[
            httpx.Response(503, text="busy"),
            httpx.Response(200, json=rpc_response),
        ]
    )
    async with A2AClient(_agent(), token="t", retry=fast_retry) as c:
        res = await c.send_message("x")
    assert res.state == "completed"
    assert route.call_count == 2


@respx.mock
async def test_unavailable_persists(fast_retry) -> None:
    respx.post("http://ta.example/a2a/").mock(return_value=httpx.Response(503, text="busy"))
    async with A2AClient(_agent(), token="t", retry=fast_retry) as c:
        with pytest.raises(RemoteAgentUnavailable):
            await c.send_message("x")


@respx.mock
async def test_timeout(fast_retry) -> None:
    respx.post("http://ta.example/a2a/").mock(side_effect=httpx.ReadTimeout("slow"))
    async with A2AClient(_agent(), token="t", retry=fast_retry) as c:
        with pytest.raises(RemoteAgentTimeout):
            await c.send_message("x")


@respx.mock
async def test_jsonrpc_error_object() -> None:
    respx.post("http://ta.example/a2a/").mock(
        return_value=httpx.Response(
            200, json={"jsonrpc": "2.0", "id": "x", "error": {"code": -32601, "message": "Method not found"}}
        )
    )
    async with A2AClient(_agent(), token="t") as c:
        with pytest.raises(RemoteAgentFailed):
            await c.send_message("x")


@respx.mock
async def test_resume_with_user_input_carries_task_id() -> None:
    captured: dict[str, object] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured["body"] = body
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "x",
                "result": {
                    "task": {
                        "id": "task-9",
                        "status": {"state": "TASK_STATE_COMPLETED"},
                        "artifacts": [{"parts": [{"text": "再開ok"}]}],
                    }
                },
            },
        )

    respx.post("http://ta.example/a2a/").mock(side_effect=_handler)
    async with A2AClient(_agent(), token="t") as c:
        res = await c.resume_with_user_input("ec-shop", task_id="task-9", context_id="ctx-3")
    assert res.state == "completed"
    body = captured["body"]
    assert body["method"] == "SendMessage"
    assert body["params"]["message"]["taskId"] == "task-9"
    assert body["params"]["message"]["contextId"] == "ctx-3"
