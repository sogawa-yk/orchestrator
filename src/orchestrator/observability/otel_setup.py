from __future__ import annotations

import logging
import threading

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .. import __version__
from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

_initialized = False
_init_lock = threading.Lock()
_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None
_logger_provider: LoggerProvider | None = None


def _build_resource(settings: Settings) -> Resource:
    attrs: dict[str, str] = {
        "service.name": settings.otel_service_name,
        "service.version": __version__,
        "deployment.environment": settings.orch_environment,
    }
    if settings.k8s_pod_name:
        attrs["k8s.pod.name"] = settings.k8s_pod_name
    if settings.k8s_namespace:
        attrs["k8s.namespace.name"] = settings.k8s_namespace
    if settings.k8s_node_name:
        attrs["k8s.node.name"] = settings.k8s_node_name
    return Resource.create(attrs)


def _init_tracer(resource: Resource, endpoint: str) -> TracerProvider:
    provider = TracerProvider(resource=resource)
    try:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("OTLP span exporter 初期化失敗: %s", e)
    trace.set_tracer_provider(provider)
    return provider


def _init_meter(resource: Resource, endpoint: str) -> MeterProvider:
    try:
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=endpoint, insecure=True),
            export_interval_millis=15_000,
        )
        provider = MeterProvider(resource=resource, metric_readers=[reader])
    except Exception as e:  # noqa: BLE001
        logger.warning("OTLP metric exporter 初期化失敗: %s", e)
        provider = MeterProvider(resource=resource)
    metrics.set_meter_provider(provider)
    return provider


def _init_logger(resource: Resource, endpoint: str) -> LoggerProvider:
    provider = LoggerProvider(resource=resource)
    try:
        provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint, insecure=True))
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("OTLP log exporter 初期化失敗: %s", e)
    set_logger_provider(provider)
    # 既存の root logger に OTel ハンドラを追加 (stdlib logging を取り込む)
    handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    logging.getLogger().addHandler(handler)
    return provider


def init_otel(settings: Settings | None = None) -> TracerProvider:
    """trace + metrics + logs の Provider を作成し OTLP gRPC 経由で送出する。"""
    global _initialized, _tracer_provider, _meter_provider, _logger_provider
    with _init_lock:
        if _initialized and _tracer_provider is not None:
            return _tracer_provider
        s = settings or get_settings()
        resource = _build_resource(s)
        endpoint = s.otel_exporter_otlp_endpoint
        _tracer_provider = _init_tracer(resource, endpoint)
        _meter_provider = _init_meter(resource, endpoint)
        _logger_provider = _init_logger(resource, endpoint)
        try:
            HTTPXClientInstrumentor().instrument()
        except Exception as e:  # noqa: BLE001
            logger.warning("httpx instrumentation 失敗: %s", e)
        # メトリクス計器を登録 (MeterProvider 設定後)
        try:
            from . import metrics as _metrics_mod

            _metrics_mod.init_metrics()
        except Exception as e:  # noqa: BLE001
            logger.warning("metrics init 失敗: %s", e)
        _initialized = True
        return _tracer_provider


def shutdown_otel() -> None:
    global _initialized, _tracer_provider, _meter_provider, _logger_provider
    with _init_lock:
        for p in (_tracer_provider, _meter_provider, _logger_provider):
            if p is not None:
                try:
                    p.shutdown()
                except Exception as e:  # noqa: BLE001
                    logger.warning("OTel provider shutdown 失敗: %s", e)
        _tracer_provider = None
        _meter_provider = None
        _logger_provider = None
        _initialized = False


def reset_for_tests() -> None:
    shutdown_otel()
    try:
        from . import metrics as _metrics_mod

        _metrics_mod.reset_for_tests()
    except Exception:  # noqa: BLE001
        pass
