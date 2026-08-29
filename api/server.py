"""
api/server.py

FastAPI application factory and server entry point.

Responsibilities:
  - Build the FastAPI app
  - Register all routers (actions, health, logs, plugins, system)
  - Mount the WebSocket endpoint
  - Bridge the internal EventBus → WebSocket clients on startup
  - Expose start_server() for main.py

Design principles:
  - Zero hardcoded config: every setting comes from core.config.settings
  - No inline route logic: all routes live in api/routes/
  - WebSocket logic lives in api/websocket.py
  - Graceful: starts even if optional components (audio, STT) aren't ready yet
"""

import logging
import uvicorn

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from core.event_bus import bus
from core.config import settings

# ── Routers ─────────────────────────────────────────────────────────────────
from api.routes.actions import router as actions_router
from api.routes.health  import router as health_router,  system_state
from api.routes.logs    import router as logs_router
from api.routes.plugins import router as plugins_router
from api.routes.system  import router as system_router

# ── WebSocket manager ────────────────────────────────────────────────────────
from api.websocket import manager as ws_manager, websocket_handler

logger = logging.getLogger("APIServer")


# ─────────────────────────────────────────────────────────────────────────────
# App factory
# ─────────────────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    """
    Build and return the FastAPI application.

    Called once at startup. Kept as a factory so it can be used in tests
    without actually starting the server.
    """
    app = FastAPI(
        title=getattr(settings, "PROJECT_NAME", "i_os Agent API"),
        version=getattr(settings, "VERSION",      "1.0.0"),
        description="Real-time control plane for the i_os autonomous agent.",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── CORS ──────────────────────────────────────────────────────────────
    allowed_origins = getattr(settings, "CORS_ORIGINS", ["*"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────
    # Each router already carries its own prefix (e.g. /api/health, /api/logs …)
    app.include_router(actions_router)
    app.include_router(health_router)
    app.include_router(logs_router)
    app.include_router(plugins_router)
    app.include_router(system_router)

    # ── WebSocket ─────────────────────────────────────────────────────────
    @app.websocket("/ws/dashboard")
    async def ws_endpoint(websocket: WebSocket):
        await websocket_handler(websocket)
    
    @app.websocket("/ws/agent")
    async def agent_ws_endpoint(websocket: WebSocket):
        """WebSocket endpoint for local agent connections in hybrid deployment"""
        await websocket_handler(websocket)

    # ── Lifecycle hooks ───────────────────────────────────────────────────
    @app.on_event("startup")
    async def on_startup():
        _setup_event_bridge()
        system_state.event_bus_running = True
        logger.info("API Server online.")

    @app.on_event("shutdown")
    async def on_shutdown():
        logger.info("API Server shutting down.")

    return app


# ─────────────────────────────────────────────────────────────────────────────
# Event bridge
# ─────────────────────────────────────────────────────────────────────────────

def _setup_event_bridge() -> None:
    """
    Subscribe the WebSocket manager to all internal EventBus events so that
    every system event is forwarded to connected dashboard clients in real time.

    The manager decides per-connection which events to deliver based on each
    client's active subscriptions.
    """
    ws_manager.attach_bus(bus)
    logger.info("Event bridge: EventBus → WebSocket clients linked.")


# ─────────────────────────────────────────────────────────────────────────────
# Server entry point
# ─────────────────────────────────────────────────────────────────────────────

def start_server() -> None:
    """
    Called by core/main.py to start the API server.

    All parameters are read from settings — no hardcoded host/port/workers.
    """
    app = create_app()

    host    = getattr(settings, "API_HOST",    "0.0.0.0")
    port    = getattr(settings, "API_PORT",    8000)
    workers = getattr(settings, "API_WORKERS", 1)
    reload  = getattr(settings, "DEBUG",       False)
    log_lvl = getattr(settings, "LOG_LEVEL",   "info").lower()

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        workers=workers if not reload else 1,   # uvicorn reload requires workers=1
        reload=reload,
        log_level=log_lvl,
        loop="asyncio",
    )

    logger.info("Starting API server on %s:%d", host, port)
    server = uvicorn.Server(config)
    server.run()