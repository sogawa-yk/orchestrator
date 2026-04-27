from __future__ import annotations

import logging
import threading

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .. import __version__
from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

_initialized = False
_init_lock = threading.Lock()
_provider: TracerProvider | None = None


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


def init_otel(settings: Settings | None = None) -> TracerProvider:
    """TracerProvider を作成し OTLP gRPC へ送る BatchSpanProcessor を取り付ける。

    プロセス内で 1 度だけ初期化される。テスト用の reset は別関数で。
    """
    global _initialized, _provider
    with _init_lock:
        if _initialized and _provider is not None:
            return _provider
        s = settings or get_settings()
        resource = _build_resource(s)
        provider = TracerProvider(resource=resource)
        try:
            exporter = OTLPSpanExporter(endpoint=s.otel_exporter_otlp_endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except Exception as e:  # noqa: BLE001
            logger.warning("OTLP exporter 初期化失敗: %s (trace は no-op で続行)", e)
        trace.set_tracer_provider(provider)
        try:
            HTTPXClientInstrumentor().instrument()
        except Exception as e:  # noqa: BLE001
            logger.warning("httpx instrumentation 失敗: %s", e)
        _provider = provider
        _initialized = True
        return provider


def shutdown_otel() -> None:
    global _initialized, _provider
    with _init_lock:
        if _provider is not None:
            try:
                _provider.shutdown()
            except Exception as e:  # noqa: BLE001
                logger.warning("OTel shutdown 失敗: %s", e)
        _provider = None
        _initialized = False


def reset_for_tests() -> None:
    shutdown_otel()
