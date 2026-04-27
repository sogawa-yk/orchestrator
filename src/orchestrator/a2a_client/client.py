from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from opentelemetry import trace
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..registry.models import AgentEntry, RetrySpec
from .errors import (
    InputRequired,
    RemoteAgentFailed,
    RemoteAgentTimeout,
    RemoteAgentUnauthorized,
    RemoteAgentUnavailable,
)

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


# A2A v1.0 Task state 値の正規化マップ (gRPC enum 名 / kebab 旧表記の両方を受ける)
_STATE_MAP = {
    "TASK_STATE_UNSPECIFIED": "unspecified",
    "TASK_STATE_SUBMITTED": "submitted",
    "TASK_STATE_WORKING": "working",
    "TASK_STATE_INPUT_REQUIRED": "input-required",
    "TASK_STATE_COMPLETED": "completed",
    "TASK_STATE_CANCELED": "canceled",
    "TASK_STATE_CANCELLED": "canceled",
    "TASK_STATE_FAILED": "failed",
    "TASK_STATE_REJECTED": "rejected",
    "TASK_STATE_AUTH_REQUIRED": "auth-required",
}
_INPUT_REQUIRED_STATES = {"input-required", "input_required", "TASK_STATE_INPUT_REQUIRED"}
_HEADER_VERSION = "1.0"


@dataclass
class CallResult:
    """A2A 呼び出しの正規化結果。"""

    final_text: str
    state: str
    task_id: str | None
    context_id: str | None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] | None = None


class A2AClient:
    """JSON-RPC ベースの A2A v1.0 クライアント。

    - `send_message`: `message/send` を呼び、Task を完了状態まで進める。
      `input-required` を検出したら `InputRequired` 例外を上げて呼び出し側で resume させる。
    - `resume_with_user_input`: 同じ task_id / context_id で `message/send` を追加投入。
    - リトライは接続系と 5xx に限定 (registry の `defaults.retry`)。401/4xx は即時失敗。
    """

    def __init__(
        self,
        agent: AgentEntry,
        token: str | None,
        *,
        timeout_seconds: int = 60,
        retry: RetrySpec | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.agent = agent
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.retry = retry or RetrySpec()
        self._client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> "A2AClient":
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds, connect=5.0)
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("A2AClient must be used as async context manager")
        return self._client

    @property
    def _endpoint(self) -> str:
        # base_url 末尾を `/` に正規化して JSON-RPC 投入先を決める
        return self.agent.base_url.rstrip("/") + "/"

    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "A2A-Version": _HEADER_VERSION,
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def send_message(
        self,
        text: str,
        *,
        context_id: str | None = None,
        skill_hint: str | None = None,
    ) -> CallResult:
        """user メッセージを 1 回送信して Task を完了/中断状態まで進める。"""
        message_id = uuid.uuid4().hex
        body_text = text if not skill_hint else f"[skill:{skill_hint}]\n{text}"
        message: dict[str, Any] = {
            "role": "ROLE_USER",
            "parts": [{"text": body_text}],
            "messageId": message_id,
        }
        if context_id:
            message["contextId"] = context_id
        params: dict[str, Any] = {"message": message}
        return await self._rpc_send_message(params)

    async def resume_with_user_input(
        self,
        text: str,
        *,
        task_id: str,
        context_id: str | None,
    ) -> CallResult:
        """`input-required` で中断した Task に追加情報を送って再開する。"""
        message_id = uuid.uuid4().hex
        message: dict[str, Any] = {
            "role": "ROLE_USER",
            "parts": [{"text": text}],
            "messageId": message_id,
            "taskId": task_id,
        }
        if context_id:
            message["contextId"] = context_id
        params = {"message": message}
        return await self._rpc_send_message(params)

    async def _rpc_send_message(self, params: dict[str, Any]) -> CallResult:
        rpc_id = uuid.uuid4().hex
        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "SendMessage",
            "params": params,
        }
        with tracer.start_as_current_span(
            "a2a.send_message",
            attributes={
                "a2a.agent.id": self.agent.id,
                "a2a.endpoint": self._endpoint,
            },
        ) as span:
            try:
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(self.retry.max_attempts),
                    wait=wait_exponential(
                        multiplier=self.retry.backoff_seconds, min=0.5, max=10
                    ),
                    retry=retry_if_exception_type(
                        (RemoteAgentUnavailable, RemoteAgentTimeout)
                    ),
                    reraise=True,
                ):
                    with attempt:
                        resp = await self._post(payload)
            except RetryError as e:
                inner = e.last_attempt.exception() if e.last_attempt else None
                if inner is not None:
                    raise inner
                raise

            result = self._parse_jsonrpc(resp)
            normalized = self._normalize_result(result)
            span.set_attribute("a2a.task.state", normalized.state)
            if normalized.task_id:
                span.set_attribute("a2a.task.id", normalized.task_id)
            if normalized.context_id:
                span.set_attribute("a2a.context.id", normalized.context_id)
            if normalized.state in _INPUT_REQUIRED_STATES:
                prompt = self._extract_prompt(normalized.raw or result)
                raise InputRequired(
                    prompt,
                    task_id=normalized.task_id,
                    context_id=normalized.context_id,
                )
            return normalized

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            resp = await self._http.post(
                self._endpoint, json=payload, headers=self._headers()
            )
        except httpx.TimeoutException as e:
            raise RemoteAgentTimeout(str(e)) from e
        except httpx.TransportError as e:
            raise RemoteAgentUnavailable(str(e)) from e
        if resp.status_code in (401, 403):
            raise RemoteAgentUnauthorized(
                f"{resp.status_code} {resp.text[:200]}"
            )
        if resp.status_code in (502, 503, 504):
            raise RemoteAgentUnavailable(
                f"{resp.status_code} {resp.text[:200]}"
            )
        if resp.status_code >= 400:
            raise RemoteAgentFailed(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return resp

    def _parse_jsonrpc(self, resp: httpx.Response) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError as e:
            raise RemoteAgentFailed(f"非 JSON 応答: {resp.text[:200]}") from e
        if "error" in data:
            err = data["error"]
            raise RemoteAgentFailed(
                f"JSON-RPC error code={err.get('code')} message={err.get('message')}"
            )
        result = data.get("result")
        if result is None:
            raise RemoteAgentFailed(f"JSON-RPC result 欠落: {data}")
        return result

    def _normalize_result(self, result: dict[str, Any]) -> CallResult:
        """a2a-sdk 1.0 の SendMessageResponse は `result.task` / `result.message` の
        oneof で返る。それ以外 (旧 kind=task/kind=message) も後方互換で受ける。
        """
        # v1.0 oneof: result.task or result.message
        if isinstance(result.get("task"), dict):
            return self._normalize_task(result["task"])
        if isinstance(result.get("message"), dict):
            return self._normalize_message(result["message"])

        # 後方互換: result そのものが Task / Message
        kind = result.get("kind")
        if kind == "message":
            return self._normalize_message(result)
        return self._normalize_task(result)

    def _normalize_message(self, message: dict[str, Any]) -> CallResult:
        text = self._collect_text_from_parts(message.get("parts") or message.get("content") or [])
        return CallResult(
            final_text=text,
            state="completed",
            task_id=None,
            context_id=message.get("contextId"),
            artifacts=[],
            raw=message,
        )

    def _normalize_task(self, task: dict[str, Any]) -> CallResult:
        status = task.get("status") or {}
        raw_state = status.get("state") or task.get("state") or "unknown"
        state = _STATE_MAP.get(str(raw_state), str(raw_state))
        artifacts = task.get("artifacts") or []
        text = self._collect_text_from_artifacts(artifacts)
        if not text:
            text = self._collect_text_from_parts(
                ((status.get("message") or {}).get("parts")) or []
            )
        return CallResult(
            final_text=text,
            state=state,
            task_id=task.get("id") or task.get("taskId"),
            context_id=task.get("contextId"),
            artifacts=artifacts,
            raw=task,
        )

    @staticmethod
    def _collect_text_from_parts(parts: list[dict[str, Any]]) -> str:
        chunks: list[str] = []
        for p in parts or []:
            t = p.get("text")
            if isinstance(t, str) and t:
                chunks.append(t)
        return "\n".join(chunks)

    @classmethod
    def _collect_text_from_artifacts(cls, artifacts: list[dict[str, Any]]) -> str:
        chunks: list[str] = []
        for a in artifacts or []:
            t = cls._collect_text_from_parts(a.get("parts") or [])
            if t:
                chunks.append(t)
        return "\n\n".join(chunks)

    @staticmethod
    def _extract_prompt(result: dict[str, Any]) -> str:
        status = result.get("status") or {}
        msg = status.get("message") or {}
        parts = msg.get("parts") or []
        for p in parts:
            t = p.get("text")
            if isinstance(t, str) and t:
                return t
        return "追加情報の入力が必要です。"
