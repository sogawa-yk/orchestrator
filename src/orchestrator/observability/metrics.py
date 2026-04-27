from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from opentelemetry import metrics

logger = logging.getLogger(__name__)

_METER_NAME = "orchestrator"
_lock = threading.Lock()
_initialized = False
_agent_calls_total: metrics.Counter | None = None
_approval_total: metrics.Counter | None = None
_tool_latency_ms: metrics.Histogram | None = None
_active_sessions: metrics.UpDownCounter | None = None


def init_metrics() -> None:
    """グローバル MeterProvider から各計器を生成する。`init_otel()` 後に呼ぶ。"""
    global _initialized, _agent_calls_total, _approval_total, _tool_latency_ms, _active_sessions
    with _lock:
        if _initialized:
            return
        meter = metrics.get_meter(_METER_NAME)
        _agent_calls_total = meter.create_counter(
            name="orchestrator_agent_calls_total",
            description="リモートエージェント呼び出し件数 (outcome: success/denied/failed/input_required_resumed)",
            unit="1",
        )
        _approval_total = meter.create_counter(
            name="orchestrator_approval_total",
            description="承認 UI の決定件数 (decision: approved/rejected/timeout)",
            unit="1",
        )
        _tool_latency_ms = meter.create_histogram(
            name="orchestrator_tool_latency_ms",
            description="ツール実行のレイテンシ (ms)",
            unit="ms",
        )
        _active_sessions = meter.create_up_down_counter(
            name="orchestrator_active_sessions",
            description="現在アクティブな Chainlit セッション数",
            unit="1",
        )
        _initialized = True


def record_agent_call(agent_id: str, skill_id: str, outcome: str) -> None:
    if _agent_calls_total is None:
        return
    _agent_calls_total.add(
        1, attributes={"agent": agent_id, "skill": skill_id, "outcome": outcome}
    )


def record_approval(agent_id: str, skill_id: str, decision: str) -> None:
    if _approval_total is None:
        return
    _approval_total.add(
        1, attributes={"agent": agent_id, "skill": skill_id, "decision": decision}
    )


def record_session_delta(delta: int) -> None:
    if _active_sessions is None:
        return
    _active_sessions.add(delta)


@contextmanager
def measure_tool_latency(tool: str) -> Iterator[None]:
    """tool 実行時間を histogram に記録するコンテキスト。"""
    if _tool_latency_ms is None:
        yield
        return
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        _tool_latency_ms.record(elapsed_ms, attributes={"tool": tool})


def reset_for_tests() -> None:
    """テストで複数回 init を呼べるようにするためのリセット。"""
    global _initialized, _agent_calls_total, _approval_total, _tool_latency_ms, _active_sessions
    with _lock:
        _initialized = False
        _agent_calls_total = None
        _approval_total = None
        _tool_latency_ms = None
        _active_sessions = None
