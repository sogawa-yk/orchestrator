from __future__ import annotations

from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as OTLPSpanExporterHTTP,
)
from opentelemetry.sdk.resources import Resource

from orchestrator.config import Settings
from orchestrator.observability.otel_setup import (
    _build_langfuse_otlp_exporter,
    _build_resource,
)


def test_resource_attributes_minimum() -> None:
    s = Settings(
        OTEL_SERVICE_NAME="orchestrator",
        ORCH_ENVIRONMENT="staging",
    )
    r: Resource = _build_resource(s)
    attrs = r.attributes
    assert attrs["service.name"] == "orchestrator"
    assert attrs["deployment.environment"] == "staging"
    # k8s.* は env 未設定なら attrs に入らない
    assert "k8s.pod.name" not in attrs


def test_resource_attributes_with_downward_api(monkeypatch) -> None:
    s = Settings(
        OTEL_SERVICE_NAME="orchestrator",
        ORCH_ENVIRONMENT="prod",
        K8S_POD_NAME="orch-abc-1",
        K8S_NAMESPACE="orchestrator",
        K8S_NODE_NAME="node-1",
    )
    r = _build_resource(s)
    attrs = r.attributes
    assert attrs["k8s.pod.name"] == "orch-abc-1"
    assert attrs["k8s.namespace.name"] == "orchestrator"
    assert attrs["k8s.node.name"] == "node-1"
    assert attrs["service.version"]  # __version__ が入る


def test_langfuse_exporter_enabled_when_keys_present() -> None:
    s = Settings(
        LANGFUSE_HOST="http://langfuse.example",
        LANGFUSE_PUBLIC_KEY="pk-test",
        LANGFUSE_SECRET_KEY="sk-test",
    )
    exporter = _build_langfuse_otlp_exporter(s)
    assert isinstance(exporter, OTLPSpanExporterHTTP)
    # endpoint と Basic ヘッダが正しく構築されていること
    assert exporter._endpoint == "http://langfuse.example/api/public/otel/v1/traces"
    auth = exporter._session.headers.get("Authorization")
    assert auth is not None and auth.startswith("Basic ")


def test_langfuse_exporter_disabled_without_keys() -> None:
    # 両方欠落
    assert _build_langfuse_otlp_exporter(Settings()) is None
    # public のみ
    assert (
        _build_langfuse_otlp_exporter(Settings(LANGFUSE_PUBLIC_KEY="pk-only")) is None
    )
    # secret のみ
    assert (
        _build_langfuse_otlp_exporter(Settings(LANGFUSE_SECRET_KEY="sk-only")) is None
    )


def test_langfuse_exporter_strips_trailing_slash() -> None:
    s = Settings(
        LANGFUSE_HOST="http://langfuse.example/",
        LANGFUSE_PUBLIC_KEY="pk",
        LANGFUSE_SECRET_KEY="sk",
    )
    exporter = _build_langfuse_otlp_exporter(s)
    assert exporter is not None
    assert exporter._endpoint == "http://langfuse.example/api/public/otel/v1/traces"
