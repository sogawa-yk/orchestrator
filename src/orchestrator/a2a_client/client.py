"""A2A v1.0 クライアント (a2a-sdk ラッパ)。

a2a-sdk 1.0.x の `BaseClient` + `JsonRpcTransport` を使い、orchestrator 既存呼び出し側
(`tools.py`) と整合する `A2AClient` / `CallResult` の薄いアダプタを提供する。

- `send_message`: `message/send` 相当を呼び、Task を完了状態まで進める。
  `input-required` を検出したら `InputRequired` 例外を上げて呼び出し側で resume させる。
- `resume_with_user_input`: 同じ task_id / context_id で `message/send` を追加投入。
- リトライは接続系と 5xx に限定 (registry の `defaults.retry`)。401/4xx は即時失敗。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from a2a.client.base_client import BaseClient
from a2a.client.client import ClientConfig
from a2a.client.errors import A2AClientError, A2AClientTimeoutError
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.utils.errors import A2AError
from a2a.types.a2a_pb2 import (
    AgentCard,
    AgentInterface,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskState,
)
from google.protobuf.json_format import MessageToDict
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


# A2A v1.0 Task state -> orchestrator 内部表記の正規化
_STATE_MAP: dict[int, str] = {
    TaskState.TASK_STATE_UNSPECIFIED: "unspecified",
    TaskState.TASK_STATE_SUBMITTED: "submitted",
    TaskState.TASK_STATE_WORKING: "working",
    TaskState.TASK_STATE_INPUT_REQUIRED: "input-required",
    TaskState.TASK_STATE_COMPLETED: "completed",
    TaskState.TASK_STATE_CANCELED: "canceled",
    TaskState.TASK_STATE_FAILED: "failed",
    TaskState.TASK_STATE_REJECTED: "rejected",
    TaskState.TASK_STATE_AUTH_REQUIRED: "auth-required",
}
_INPUT_REQUIRED_STATE = TaskState.TASK_STATE_INPUT_REQUIRED


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
    """a2a-sdk ベースの A2A v1.0 クライアント (orchestrator 用アダプタ)。"""

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
        return self.agent.base_url.rstrip("/") + "/"

    def _bootstrap_card(self) -> AgentCard:
        """SDK Client 構築用の最小 AgentCard を作る (実際の AgentCard 取得は card_cache が担当)。"""
        return AgentCard(
            name=self.agent.id,
            supported_interfaces=[
                AgentInterface(
                    url=self._endpoint,
                    protocol_binding="JSONRPC",
                )
            ],
        )

    def _build_sdk_client(self) -> BaseClient:
        """a2a-sdk の BaseClient を共有 httpx.AsyncClient で組む。

        共有 httpx クライアントを使うので、token ヘッダの注入と respx でのテスト
        モックがそのまま動く。
        """
        http = self._http
        if self.token:
            http.headers["Authorization"] = f"Bearer {self.token}"
        # 既存実装と同じく "A2A-Version: 1.0" を確実に乗せる (SDK は別ヘッダ名を使うため)
        http.headers.setdefault("A2A-Version", "1.0")
        card = self._bootstrap_card()
        config = ClientConfig(httpx_client=http, streaming=False, polling=False)
        transport = JsonRpcTransport(
            httpx_client=http, agent_card=card, url=self._endpoint
        )
        return BaseClient(card, config, transport, interceptors=[])

    def _build_request(
        self,
        text: str,
        *,
        context_id: str | None = None,
        task_id: str | None = None,
        skill_hint: str | None = None,
    ) -> SendMessageRequest:
        body_text = text if not skill_hint else f"[skill:{skill_hint}]\n{text}"
        message = Message(
            role=Role.ROLE_USER,
            parts=[Part(text=body_text)],
            message_id=uuid.uuid4().hex,
        )
        if context_id:
            message.context_id = context_id
        if task_id:
            message.task_id = task_id
        return SendMessageRequest(
            message=message,
            configuration=SendMessageConfiguration(),
        )

    async def send_message(
        self,
        text: str,
        *,
        context_id: str | None = None,
        skill_hint: str | None = None,
    ) -> CallResult:
        """user メッセージを 1 回送信して Task を完了/中断状態まで進める。"""
        request = self._build_request(
            text, context_id=context_id, skill_hint=skill_hint
        )
        return await self._invoke(request)

    async def resume_with_user_input(
        self,
        text: str,
        *,
        task_id: str,
        context_id: str | None,
    ) -> CallResult:
        """`input-required` で中断した Task に追加情報を送って再開する。"""
        request = self._build_request(
            text, context_id=context_id, task_id=task_id
        )
        return await self._invoke(request)

    async def _invoke(self, request: SendMessageRequest) -> CallResult:
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
                        response = await self._send_once(request)
            except RetryError as e:
                inner = e.last_attempt.exception() if e.last_attempt else None
                if inner is not None:
                    raise inner
                raise

            normalized = self._normalize_response(response)
            span.set_attribute("a2a.task.state", normalized.state)
            if normalized.task_id:
                span.set_attribute("a2a.task.id", normalized.task_id)
            if normalized.context_id:
                span.set_attribute("a2a.context.id", normalized.context_id)
            if normalized.state == "input-required":
                prompt = self._extract_prompt(normalized.raw or {})
                raise InputRequired(
                    prompt,
                    task_id=normalized.task_id,
                    context_id=normalized.context_id,
                )
            return normalized

    async def _send_once(self, request: SendMessageRequest) -> StreamResponse:
        sdk_client = self._build_sdk_client()
        try:
            async for stream_response in sdk_client.send_message(request):
                return stream_response
        except httpx.TimeoutException as e:
            raise RemoteAgentTimeout(str(e)) from e
        except httpx.TransportError as e:
            raise RemoteAgentUnavailable(str(e)) from e
        except A2AClientTimeoutError as e:
            raise RemoteAgentTimeout(str(e)) from e
        except A2AError as e:
            self._raise_for_a2a_error(e)
        except httpx.HTTPStatusError as e:
            self._raise_for_status(e.response)
        raise RemoteAgentFailed("空応答")

    @staticmethod
    def _raise_for_a2a_error(exc: A2AError) -> None:
        message = str(exc)
        # SDK が発する HTTP エラーは status を文字列に持つことが多い。
        # より具体的な内訳が読めない場合はまとめて RemoteAgentFailed として扱う。
        if any(code in message for code in ("401", "403")):
            raise RemoteAgentUnauthorized(message) from exc
        if any(code in message for code in ("502", "503", "504")):
            raise RemoteAgentUnavailable(message) from exc
        raise RemoteAgentFailed(message) from exc

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code in (401, 403):
            raise RemoteAgentUnauthorized(
                f"{resp.status_code} {resp.text[:200]}"
            )
        if resp.status_code in (502, 503, 504):
            raise RemoteAgentUnavailable(
                f"{resp.status_code} {resp.text[:200]}"
            )
        if resp.status_code >= 400:
            raise RemoteAgentFailed(
                f"HTTP {resp.status_code}: {resp.text[:200]}"
            )

    def _normalize_response(self, response: StreamResponse) -> CallResult:
        if response.HasField("task"):
            return self._normalize_task(response.task)
        if response.HasField("message"):
            return self._normalize_message(response.message)
        raise RemoteAgentFailed("StreamResponse に task/message いずれも未設定")

    def _normalize_task(self, task: Task) -> CallResult:
        state = _STATE_MAP.get(task.status.state, "unknown")
        artifacts_raw = [
            MessageToDict(a, preserving_proto_field_name=False)
            for a in task.artifacts
        ]
        text = self._collect_text_from_artifacts(artifacts_raw)
        if not text and task.status.HasField("message"):
            text = self._collect_text_from_parts(
                MessageToDict(
                    task.status.message, preserving_proto_field_name=False
                ).get("parts", [])
            )
        return CallResult(
            final_text=text,
            state=state,
            task_id=task.id or None,
            context_id=task.context_id or None,
            artifacts=artifacts_raw,
            raw=MessageToDict(task, preserving_proto_field_name=False),
        )

    def _normalize_message(self, message: Message) -> CallResult:
        raw = MessageToDict(message, preserving_proto_field_name=False)
        text = self._collect_text_from_parts(raw.get("parts", []))
        return CallResult(
            final_text=text,
            state="completed",
            task_id=None,
            context_id=raw.get("contextId"),
            artifacts=[],
            raw=raw,
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
    def _collect_text_from_artifacts(
        cls, artifacts: list[dict[str, Any]]
    ) -> str:
        chunks: list[str] = []
        for a in artifacts or []:
            t = cls._collect_text_from_parts(a.get("parts") or [])
            if t:
                chunks.append(t)
        return "\n\n".join(chunks)

    @staticmethod
    def _extract_prompt(raw_task: dict[str, Any]) -> str:
        status = raw_task.get("status") or {}
        msg = status.get("message") or {}
        parts = msg.get("parts") or []
        for p in parts:
            t = p.get("text")
            if isinstance(t, str) and t:
                return t
        return "追加情報の入力が必要です。"
