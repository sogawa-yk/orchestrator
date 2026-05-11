"""A2A v1.0 → v0.3 翻訳 Executor.

orchestrator (v1.0) から SendMessage で受け取ったテキストを ri_v10 の
A2A v0.3 JSON-RPC (`method: "message/send"`) で 1 回 POST し、
返却 `result.parts[].text` (`kind="text"` のみ) を結合して
A2A v1.0 Message として enqueue する.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from a2a.helpers.proto_helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import Role

from bridge_ri.config import Settings, load_settings

logger = logging.getLogger(__name__)


class RiBridgeExecutor(AgentExecutor):
    """ri_v10 (A2A v0.3) への httpx ベース薄ブリッジ."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or load_settings()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_text = context.get_user_input() or ""
        ctx_id = context.context_id or ""

        if not user_text.strip():
            await event_queue.enqueue_event(
                new_text_message(
                    text="入力テキストが空です。",
                    context_id=ctx_id,
                    role=Role.ROLE_AGENT,
                )
            )
            return

        try:
            answer = await self._call_upstream(user_text, ctx_id)
        except httpx.TimeoutException as exc:
            logger.warning("ri_v10 upstream timeout: %s", exc)
            answer = f"ri_v10 がタイムアウトしました ({self._settings.upstream_timeout_sec}s 超過)。"
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "ri_v10 upstream HTTP %s: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            answer = (
                f"ri_v10 が HTTP {exc.response.status_code} を返しました。"
                "しばらくしてから再試行してください。"
            )
        except httpx.TransportError as exc:
            logger.warning("ri_v10 upstream connection error: %s", exc)
            answer = "ri_v10 への接続に失敗しました。NetworkPolicy / Service 設定を確認してください。"
        except Exception as exc:  # noqa: BLE001
            logger.exception("ri_v10 upstream unexpected error")
            answer = f"内部エラー: {type(exc).__name__}: {exc}"

        await event_queue.enqueue_event(
            new_text_message(text=answer, context_id=ctx_id, role=Role.ROLE_AGENT)
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None

    async def _call_upstream(self, user_text: str, ctx_id: str) -> str:
        """ri_v10 に v0.3 message/send を 1 回投げ、結合した text を返す."""
        message: dict[str, Any] = {
            "messageId": uuid.uuid4().hex,
            "role": "user",
            "parts": [{"kind": "text", "text": user_text}],
        }
        if ctx_id:
            message["contextId"] = ctx_id
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "message/send",
            "params": {"message": message},
        }

        timeout = httpx.Timeout(
            self._settings.upstream_timeout_sec, connect=5.0
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(self._settings.upstream_url, json=payload)
            resp.raise_for_status()
            body = resp.json()

        if "error" in body:
            err = body.get("error") or {}
            return (
                f"ri_v10 が JSON-RPC error を返しました "
                f"(code={err.get('code')!r}, message={err.get('message')!r})。"
            )
        result = body.get("result") or {}
        parts = result.get("parts") or []
        chunks = [
            str(p.get("text") or "")
            for p in parts
            if isinstance(p, dict) and p.get("kind") == "text"
        ]
        text = "\n".join(c for c in chunks if c).strip()
        return text or "(ri_v10 が空応答を返しました)"
