"""bridge-ri Starlette app.

- POST /                          : JSON-RPC SendMessage (a2a-sdk v1.0)
- GET  /.well-known/agent-card.json : AgentCard (v1.0 形式)

`RI_BRIDGE_A2A_TOKEN` が設定されていれば Bearer 認証必須.
"""
from __future__ import annotations

import logging

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from bridge_ri.agent_card import build_agent_card
from bridge_ri.config import load_settings
from bridge_ri.executor import RiBridgeExecutor

logger = logging.getLogger(__name__)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """`RI_BRIDGE_A2A_TOKEN` env が設定されていれば Bearer 検証する."""

    async def dispatch(self, request: Request, call_next):
        # /healthz は無認証で expose (k8s liveness/readiness probe 用)
        if request.url.path == "/healthz":
            return await call_next(request)
        expected = load_settings().bridge_auth_token
        if expected:
            header = request.headers.get("authorization", "")
            if not header.lower().startswith("bearer "):
                return JSONResponse(
                    {"error": "missing_or_invalid_authorization"}, status_code=401
                )
            token = header[7:].strip()
            if token != expected:
                return JSONResponse({"error": "invalid_token"}, status_code=403)
        return await call_next(request)


def build_a2a_app() -> Starlette:
    agent_card = build_agent_card()
    handler = DefaultRequestHandler(
        agent_executor=RiBridgeExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    routes: list = []
    routes.extend(create_jsonrpc_routes(handler, rpc_url="/"))
    routes.extend(create_agent_card_routes(agent_card))

    async def healthz(_: Request) -> JSONResponse:  # liveness/readiness
        return JSONResponse({"status": "ok"})

    from starlette.routing import Route

    routes.append(Route("/healthz", healthz, methods=["GET"]))

    return Starlette(
        routes=routes,
        middleware=[Middleware(BearerAuthMiddleware)],
    )


app = build_a2a_app()
