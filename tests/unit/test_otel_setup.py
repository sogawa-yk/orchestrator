from __future__ import annotations

from opentelemetry.sdk.resources import Resource

from orchestrator.config import Settings
from orchestrator.observability.otel_setup import _build_resource


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
