from __future__ import annotations

import logging
import sys

import structlog
from opentelemetry import trace


def _add_otel_trace_ids(
    logger: object, method_name: str, event_dict: dict
) -> dict:
    """OTel current span の trace_id / span_id を log に付与する processor。"""
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if ctx and ctx.is_valid:
        event_dict.setdefault("trace_id", format(ctx.trace_id, "032x"))
        event_dict.setdefault("span_id", format(ctx.span_id, "016x"))
    return event_dict


def setup_logging(level: str = "INFO") -> None:
    """structlog + stdlib logging を JSON 出力に統一する。

    - stdlib logging は structlog の formatter を経由して JSON にする
    - structlog のロガー (`structlog.get_logger()`) も同じ JSON を出す
    - trace_id / span_id を OTel current span から自動注入
    - OTel LoggingHandler は init_otel() 側で root logger に追加されるので、
      ここでは触らない
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        _add_otel_trace_ids,
    ]

    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )

    root = logging.getLogger()
    # 既存 stdout ハンドラを置換 (Chainlit / uvicorn が独自に追加するもの)
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler) and h.stream in (sys.stdout, sys.stderr):
            root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn / chainlit / openai 系のロガーも root へ流す
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "chainlit", "openai", "openai.agents", "httpx"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True


def get_logger(name: str | None = None) -> "structlog.stdlib.BoundLogger":
    return structlog.get_logger(name) if name else structlog.get_logger()
