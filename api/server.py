import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from core.event_bus import bus
import json

app = FastAPI(title="AI OS Agent Dashboard API")

# Allow the frontend to talk to the backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Keep track of active dashboard connections
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

# --- THE EVENT BRIDGE ---
# This function listens to the EventBus and sends EVERYTHING to the Web UI
async def event_bridge_task():
    """
    Subscribes to all major events and broadcasts them via WebSocket.
    """
    async def forward_to_web(event):
        payload = {
            "event": event.name,
            "source": event.source,
            "data": event.data,
            "timestamp": event.timestamp
        }
        await manager.broadcast(json.dumps(payload))

    # Subscribe to every lifecycle event we've created
    event_types = [
        "user_input_received", "intent_parsed", "plan_ready", 
        "execution_step_started", "execution_step_success", 
        "task_completed", "task_failed", "file_op_started", "shell_op_started"
    ]
    
    for etype in event_types:
        bus.subscribe(etype, forward_to_web)

@app.on_event("startup")
async def startup_event():
    # Start the bridge as a background task
    asyncio.create_task(event_bridge_task())

@app.websocket("/ws/dashboard")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/system/status")
async def get_status():
    return {"status": "online", "os": "detected"}