"""A2A v1.0 JSON-RPC クライアント (httpx 直叩き)。

a2a-sdk 1.0.x は protobuf 化していて API が大きく変わっており、ta-agent runbook の例
(旧 pydantic API) と互換性がない。ワイヤプロトコル (JSON-RPC) は安定しているため
SDK には依存せず httpx + JSON-RPC で直接通信する。
"""
from .auth import resolve_bearer_token
from .client import A2AClient, CallResult
from .errors import (
    A2AError,
    InputRequired,
    RemoteAgentFailed,
    RemoteAgentTimeout,
    RemoteAgentUnauthorized,
    RemoteAgentUnavailable,
)

__all__ = [
    "A2AClient",
    "A2AError",
    "CallResult",
    "InputRequired",
    "RemoteAgentFailed",
    "RemoteAgentTimeout",
    "RemoteAgentUnauthorized",
    "RemoteAgentUnavailable",
    "resolve_bearer_token",
]
