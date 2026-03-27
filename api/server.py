import asyncio
import json
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from core.event_bus import bus

app = FastAPI(title="i_os Agent Dashboard API")

# Enable CORS for the Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()

# --- THE EVENT BRIDGE ---
# This connects the internal system logic to the external Web UI
def setup_event_bridge():
    @bus.subscribe_async("*") 
    async def forward_to_dashboard(event):
        payload = {
            "source": event.source,
            "event_type": event.event_type,
            "data": event.data
        }
        await manager.broadcast(payload)

@app.on_event("startup")
async def startup_event():
    setup_event_bridge()
    print("🌐 API Server: Bridge to Event Bus is LIVE.")

# --- ROUTES (Aligned with your api/routes/ structure) ---

@app.get("/api/system/status")
async def get_status():
    """Matches api/routes/system.py logic"""
    import platform
    return {
        "status": "online",
        "os": platform.system(),
        "version": "1.0.0",
        "engine": "i_os_core"
    }

@app.get("/api/logs/recent")
async def get_recent_logs():
    """Matches api/routes/logs.py logic"""
    # This would eventually read from your /logs/ directory
    return {"logs": ["System started...", "Event bus initialized..."]}

@app.websocket("/ws/dashboard")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Receive commands from the Dashboard (e.g., "STOP", "RETRY")
            data = await websocket.receive_text()
            message = json.loads(data)
            
            # Emit back to the system so Orchestrator/Executor can react
            await bus.emit("dashboard_command", message, source="api_server")
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)

def start_server():
    """Main entry point called by main.py"""
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info", loop="asyncio")
    server = uvicorn.Server(config)
    return server.serve()