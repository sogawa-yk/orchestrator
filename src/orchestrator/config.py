from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Orchestrator 全体の設定。env / ConfigMap / Secret から読み込む。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # LLM (OCI Enterprise AI / OpenAI 互換)
    openai_base_url: str = Field(
        default="https://inference.generativeai.ap-osaka-1.oci.oraclecloud.com/openai/v1",
        alias="OPENAI_BASE_URL",
    )
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    orch_model: str = Field(default="openai.gpt-4.1", alias="ORCH_MODEL")
    oci_genai_project: str = Field(default="", alias="OCI_GENAI_PROJECT")

    # Registry
    orch_agents_path: Path = Field(
        default=Path("/etc/orchestrator/agents.yaml"), alias="ORCH_AGENTS_PATH"
    )
    a2a_agents_json: str = Field(default="", alias="A2A_AGENTS_JSON")

    # OTel
    otel_exporter_otlp_endpoint: str = Field(
        default="http://otel-gateway-opentelemetry-collector.observability:4317",
        alias="OTEL_EXPORTER_OTLP_ENDPOINT",
    )
    otel_exporter_otlp_protocol: str = Field(default="grpc", alias="OTEL_EXPORTER_OTLP_PROTOCOL")
    otel_service_name: str = Field(default="orchestrator", alias="OTEL_SERVICE_NAME")

    # Langfuse
    langfuse_host: str = Field(
        default="http://langfuse-web.langfuse.svc.cluster.local:3000", alias="LANGFUSE_HOST"
    )
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")

    # Behaviour
    orch_log_level: str = Field(default="INFO", alias="ORCH_LOG_LEVEL")
    orch_request_timeout_sec: int = Field(default=120, alias="ORCH_REQUEST_TIMEOUT_SEC")
    orch_environment: str = Field(default="local", alias="ORCH_ENVIRONMENT")

    # Downward API (Pod 情報)
    k8s_pod_name: str = Field(default="", alias="K8S_POD_NAME")
    k8s_namespace: str = Field(default="", alias="K8S_NAMESPACE")
    k8s_node_name: str = Field(default="", alias="K8S_NODE_NAME")


_settings: Settings | None = None


def get_settings() -> Settings:
    """Settings をプロセス内で 1 度だけ生成して返す。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_for_tests() -> None:
    """テストで env を変えて再評価したい時のみ呼ぶ。"""
    global _settings
    _settings = None
