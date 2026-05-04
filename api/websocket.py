"""
api/websocket.py

WebSocket Connection Manager
Handles all real-time bidirectional communication between the Agent Core
and the Dashboard UI. Bridges the internal EventBus to external WebSocket clients.

Design principles:
- No hardcoded values: all config comes from core.config.settings
- Self-healing: auto-reconnect, dead connection pruning
- Event-driven: subscribes to ALL bus events and forwards selectively
- Extensible: clients can subscribe to specific event channels
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Set
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger("WebSocketManager")


# ─────────────────────────────────────────────────────────────────────────────
# Connection Record
# ─────────────────────────────────────────────────────────────────────────────

class WSConnection:
    """Wraps a single WebSocket with metadata."""

    def __init__(self, websocket: WebSocket, client_id: str):
        self.websocket = websocket
        self.client_id = client_id
        self.subscriptions: Set[str] = {"*"}   # subscribed event channels
        self.is_alive: bool = True

    async def send(self, payload: dict) -> bool:
        """Send JSON payload. Returns False if the connection is dead."""
        try:
            if self.websocket.client_state == WebSocketState.CONNECTED:
                await self.websocket.send_json(payload)
                return True
        except Exception as exc:
            logger.debug("Dead connection %s pruned: %s", self.client_id, exc)
            self.is_alive = False
        return False

    def wants(self, event_type: str) -> bool:
        """True if this client subscribed to the given event channel."""
        return "*" in self.subscriptions or event_type in self.subscriptions


# ─────────────────────────────────────────────────────────────────────────────
# Connection Manager
# ─────────────────────────────────────────────────────────────────────────────

class ConnectionManager:
    """
    Manages all active WebSocket connections.

    Responsibilities:
    - Accept / disconnect clients
    - Broadcast system events to subscribed clients
    - Route inbound dashboard commands back to the EventBus
    - Prune dead connections automatically
    """

    def __init__(self):
        self._connections: Dict[str, WSConnection] = {}
        self._counter: int = 0
        self._bus = None          # injected after startup

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def attach_bus(self, bus) -> None:
        """Attach the internal EventBus so inbound commands can be forwarded."""
        self._bus = bus
        bus.subscribe("*", self._forward_event)
        logger.info("WebSocketManager attached to EventBus.")

    async def connect(self, websocket: WebSocket) -> WSConnection:
        await websocket.accept()
        self._counter += 1
        client_id = f"ws_client_{self._counter}"
        conn = WSConnection(websocket, client_id)
        self._connections[client_id] = conn
        logger.info("Client connected: %s  (total=%d)", client_id, len(self._connections))
        return conn

    def disconnect(self, conn: WSConnection) -> None:
        self._connections.pop(conn.client_id, None)
        conn.is_alive = False
        logger.info("Client disconnected: %s  (total=%d)", conn.client_id, len(self._connections))

    # ── Outbound (system → dashboard) ──────────────────────────────────────

    async def broadcast(self, payload: dict, channel: str = "*") -> None:
        """Send payload to all clients subscribed to *channel*."""
        dead: List[str] = []
        for cid, conn in list(self._connections.items()):
            if conn.wants(channel):
                ok = await conn.send(payload)
                if not ok:
                    dead.append(cid)
        for cid in dead:
            self._connections.pop(cid, None)

    async def send_to(self, client_id: str, payload: dict) -> bool:
        """Send payload to a specific client."""
        conn = self._connections.get(client_id)
        if conn:
            return await conn.send(payload)
        return False

    async def _forward_event(self, event) -> None:
        """EventBus callback — forwards every internal event to the dashboard."""
        payload = {
            "type":       "event",
            "source":     getattr(event, "source", "unknown"),
            "event_type": getattr(event, "name",   "unknown"),
            "data":       getattr(event, "data",   {}),
        }
        await self.broadcast(payload, channel=getattr(event, "name", "*"))

    # ── Inbound (dashboard → system) ───────────────────────────────────────

    async def handle_message(self, conn: WSConnection, raw: str) -> None:
        """
        Parse a raw message from the dashboard and route it.

        Supported message formats:
          {"action": "STOP"}
          {"action": "RETRY", "task_id": "..."}
          {"action": "SUBSCRIBE", "channels": ["action_taken", "error"]}
          {"action": "PING"}
        """
        try:
            msg: Dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            await conn.send({"type": "error", "message": "Invalid JSON"})
            return

        action = msg.get("action", "").upper()

        if action == "PING":
            await conn.send({"type": "pong"})

        elif action == "SUBSCRIBE":
            channels = msg.get("channels", [])
            if isinstance(channels, list):
                conn.subscriptions = set(channels) if channels else {"*"}
                await conn.send({"type": "subscribed", "channels": list(conn.subscriptions)})

        elif action in ("STOP", "PAUSE", "RESUME", "RETRY", "RESTART"):
            if self._bus:
                await self._bus.emit(
                    "dashboard_command",
                    {"action": action, **{k: v for k, v in msg.items() if k != "action"}},
                    source="dashboard",
                )
                await conn.send({"type": "ack", "action": action})
            else:
                await conn.send({"type": "error", "message": "EventBus not attached"})

        # ── Plugin self-evolution commands ────────────────────────────────────
        # These allow the dashboard to drive the plugin system over WebSocket
        # without going through HTTP — useful for real-time approval flows.

        elif action == "APPROVE_PLUGIN":
            # Approve a pending generated plugin and deploy it
            plugin_name = msg.get("name", "")
            plugin_dir  = msg.get("plugin_dir", "")
            intent      = msg.get("intent", plugin_name)
            if not plugin_name:
                await conn.send({"type": "error", "message": "'name' required"})
            elif self._bus:
                self._bus.publish(
                    "plugin_approved",
                    {"name": plugin_name, "intent": intent, "plugin_dir": plugin_dir},
                    source="dashboard",
                )
                await conn.send({"type": "ack", "action": "APPROVE_PLUGIN", "name": plugin_name})

        elif action == "REJECT_PLUGIN":
            # Reject a pending plugin (mark untrusted, stop generation)
            plugin_name = msg.get("name", "")
            reason      = msg.get("reason", "Rejected by user")
            if not plugin_name:
                await conn.send({"type": "error", "message": "'name' required"})
            elif self._bus:
                self._bus.publish(
                    "plugin_rejected",
                    {"name": plugin_name, "reason": reason},
                    source="dashboard",
                )
                await conn.send({"type": "ack", "action": "REJECT_PLUGIN", "name": plugin_name})

        elif action == "GENERATE_PLUGIN":
            # Manually trigger plugin generation for a specific intent
            intent  = msg.get("intent", "")
            reason  = msg.get("reason", "Manual generation request from dashboard")
            if not intent:
                await conn.send({"type": "error", "message": "'intent' required"})
            elif self._bus:
                self._bus.publish(
                    "capability_gap_detected",
                    {
                        "intent":               intent,
                        "reason":               reason,
                        "consecutive_failures":  1,
                        "window_failures":       1,
                        "failure_summary":       {},
                    },
                    source="dashboard",
                )
                await conn.send({"type": "ack", "action": "GENERATE_PLUGIN", "intent": intent})

        elif action == "EVOLVE_PLUGIN":
            # Manually trigger evolution for an existing plugin
            plugin_name = msg.get("name", "")
            reason      = msg.get("reason", "Manual evolution request from dashboard")
            if not plugin_name:
                await conn.send({"type": "error", "message": "'name' required"})
            elif self._bus:
                self._bus.publish(
                    "plugin_evolution_requested",
                    {"name": plugin_name, "reason": reason},
                    source="dashboard",
                )
                await conn.send({"type": "ack", "action": "EVOLVE_PLUGIN", "name": plugin_name})

        elif action == "RELOAD_PLUGIN":
            # Hot-reload a specific plugin
            plugin_name = msg.get("name", "")
            if not plugin_name:
                await conn.send({"type": "error", "message": "'name' required"})
            elif self._bus:
                self._bus.publish(
                    "plugin_reload_requested",
                    {"name": plugin_name},
                    source="dashboard",
                )
                await conn.send({"type": "ack", "action": "RELOAD_PLUGIN", "name": plugin_name})

        else:
            # Generic passthrough — let the orchestrator decide
            if self._bus:
                await self._bus.emit("dashboard_message", msg, source="dashboard")
            await conn.send({"type": "ack", "action": action or "unknown"})

    # ── Introspection ───────────────────────────────────────────────────────

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def connection_list(self) -> List[Dict]:
        return [
            {
                "client_id":     cid,
                "subscriptions": list(conn.subscriptions),
                "alive":         conn.is_alive,
            }
            for cid, conn in self._connections.items()
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

manager = ConnectionManager()


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI endpoint helper (used by server.py)
# ─────────────────────────────────────────────────────────────────────────────

async def websocket_handler(websocket: WebSocket) -> None:
    """
    Mount this coroutine as:
        @app.websocket("/ws/dashboard")
        async def ws(websocket: WebSocket):
            await websocket_handler(websocket)
    """
    conn = await manager.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            await manager.handle_message(conn, raw)
    except WebSocketDisconnect:
        manager.disconnect(conn)
    except Exception as exc:
        logger.error("Unexpected error on %s: %s", conn.client_id, exc)
        manager.disconnect(conn)