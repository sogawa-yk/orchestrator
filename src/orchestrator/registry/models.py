from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RetrySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = 2
    backoff_seconds: float = 1.5


class DefaultsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_seconds: int = 60
    retry: RetrySpec = Field(default_factory=RetrySpec)
    card_cache_ttl_seconds: int = 600


class AuthSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["bearer", "none"] = "bearer"
    token_env: str | None = None


class SkillPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requires_approval: bool | None = None
    require_reason_input: bool = False
    reason: str | None = None


class ApprovalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default: Literal["required", "not_required"] = "not_required"
    skills: dict[str, SkillPolicy] = Field(default_factory=dict)


class AgentEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    base_url: str
    auth: AuthSpec = Field(default_factory=AuthSpec)
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    approval: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    notes: str | None = None


class Registry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    defaults: DefaultsSpec = Field(default_factory=DefaultsSpec)
    agents: list[AgentEntry] = Field(default_factory=list)

    def get(self, agent_id: str) -> AgentEntry | None:
        for a in self.agents:
            if a.id == agent_id:
                return a
        return None

    def enabled_agents(self) -> list[AgentEntry]:
        return [a for a in self.agents if a.enabled]
