"""AgentRegistry: ConfigMap (orchestrator-agents) と AgentCard を組み合わせ、
利用可能なリモートエージェントの一覧と承認ポリシーを提供する。"""
from .loader import load_registry
from .models import (
    AgentEntry,
    ApprovalPolicy,
    AuthSpec,
    DefaultsSpec,
    Registry,
    RetrySpec,
    SkillPolicy,
)

__all__ = [
    "AgentEntry",
    "ApprovalPolicy",
    "AuthSpec",
    "DefaultsSpec",
    "Registry",
    "RetrySpec",
    "SkillPolicy",
    "load_registry",
]
