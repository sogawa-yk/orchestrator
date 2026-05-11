"""ブリッジ設定 (env から読み取り)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    upstream_url: str
    """ri_v10 の JSON-RPC エンドポイント (v0.3, default は cluster 内 Service)."""

    bridge_auth_token: str
    """orchestrator → bridge-ri 間の Bearer (空文字なら認証無効)."""

    upstream_timeout_sec: float
    """ri_v10 への httpx タイムアウト秒."""


def load_settings() -> Settings:
    return Settings(
        upstream_url=os.getenv(
            "RI_UPSTREAM_URL",
            "http://resource-intelligence.resource-intelligence.svc:443/a2a",
        ),
        bridge_auth_token=os.getenv("RI_BRIDGE_A2A_TOKEN", "").strip(),
        upstream_timeout_sec=float(os.getenv("RI_UPSTREAM_TIMEOUT", "60")),
    )
