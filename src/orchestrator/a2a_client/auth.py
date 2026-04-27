from __future__ import annotations

import os

from ..registry.models import AgentEntry


def resolve_bearer_token(agent: AgentEntry) -> str | None:
    """AgentEntry.auth から Bearer Token を解決する。

    現状は env 経由のみ。Pod の Deployment で secretKeyRef を env として注入する想定。
    """
    if agent.auth.kind != "bearer":
        return None
    env_name = agent.auth.token_env
    if not env_name:
        return None
    token = os.environ.get(env_name)
    if not token:
        return None
    return token
