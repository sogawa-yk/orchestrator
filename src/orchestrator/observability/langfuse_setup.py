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

    Public/Secret key が未設定なら None を返す (Langfuse 連携無効化)。
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
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Langfuse client 初期化失敗: %s", e)
            _lf_client = None
        return _lf_client


def build_langfuse_openai_client(settings: Settings | None = None) -> Any:
    """`langfuse.openai.AsyncOpenAI` で OCI Enterprise AI を呼ぶための AsyncOpenAI を返す。

    Langfuse 設定が無ければ素の `openai.AsyncOpenAI` を返す。
    """
    s = settings or get_settings()
    if s.langfuse_public_key and s.langfuse_secret_key:
        try:
            from langfuse.openai import AsyncOpenAI  # type: ignore[import-not-found]

            return AsyncOpenAI(
                api_key=s.openai_api_key,
                base_url=s.openai_base_url,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Langfuse openai ラッパ生成失敗、素の openai に fallback: %s", e)
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=s.openai_api_key, base_url=s.openai_base_url)


def reset_for_tests() -> None:
    global _lf_client
    with _lf_lock:
        _lf_client = None
