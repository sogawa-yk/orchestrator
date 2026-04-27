from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class CachedCard:
    card: dict[str, Any]
    expires_at: float


class AgentCardCache:
    """AgentCard (`/.well-known/agent-card.json`) を取得し TTL でメモリキャッシュする。

    A2A 仕様の `/a2a/.well-known/agent-card.json` を 1 つの HTTP GET で取得するだけなので
    SDK には依存しない。Bearer 認証も同じ httpx クライアント経由で適用する。
    """

    def __init__(self, ttl_seconds: int = 600) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[str, CachedCard] = {}
        self._lock = asyncio.Lock()

    def _now(self) -> float:
        return time.monotonic()

    async def get(
        self,
        agent_id: str,
        base_url: str,
        token: str | None,
        *,
        client: httpx.AsyncClient | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        async with self._lock:
            cached = self._cache.get(agent_id)
            if not force_refresh and cached and cached.expires_at > self._now():
                return cached.card
        # base_url の末尾スラッシュを正規化
        url = base_url.rstrip("/") + "/.well-known/agent-card.json"
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        owns_client = client is None
        c = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
        try:
            resp = await c.get(url, headers=headers)
            resp.raise_for_status()
            card = resp.json()
        finally:
            if owns_client:
                await c.aclose()
        async with self._lock:
            self._cache[agent_id] = CachedCard(card=card, expires_at=self._now() + self._ttl)
        return card

    def invalidate(self, agent_id: str | None = None) -> None:
        if agent_id is None:
            self._cache.clear()
        else:
            self._cache.pop(agent_id, None)
