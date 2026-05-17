from __future__ import annotations

import logging
import threading
from typing import Any

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

_lf_client: Any | None = None
_lf_lock = threading.Lock()


def get_langfuse_client(settings: Settings | None = None) -> Any | None:
    """Langfuse SDK クライアントをプロセス内で 1 度だけ生成して返す。

    Trace 出力経路は `otel_setup._init_tracer` が登録する OTLP/HTTP exporter に
    一本化しているため、ここでは `tracing_enabled=False` を指定し SDK 側の
    TracerProvider 自動セットアップを抑止する。SDK は Datasets / Score 等の
    API 用途に限定して使う。Public/Secret key が未設定なら None を返す。
    """
    global _lf_client
    s = settings or get_settings()
    if not s.langfuse_public_key or not s.langfuse_secret_key:
        return None
    with _lf_lock:
        if _lf_client is not None:
            return _lf_client
        try:
            from langfuse import Langfuse  # type: ignore[import-not-found]

            _lf_client = Langfuse(
                public_key=s.langfuse_public_key,
                secret_key=s.langfuse_secret_key,
                host=s.langfuse_host,
                tracing_enabled=False,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Langfuse client 初期化失敗: %s", e)
            _lf_client = None
        return _lf_client


def build_openai_client(settings: Settings | None = None) -> Any:
    """OCI Enterprise AI 向け `openai.AsyncOpenAI` を返す。

    Langfuse への LLM trace は OTel 側 (OpenAIAgentsInstrumentor) で取得する設計のため、
    ここでは `langfuse.openai` ラッパは使わない (使うと LLM call が二重計上される)。
    """
    s = settings or get_settings()
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=s.openai_api_key, base_url=s.openai_base_url)


def reset_for_tests() -> None:
    global _lf_client
    with _lf_lock:
        _lf_client = None
