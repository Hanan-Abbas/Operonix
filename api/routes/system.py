"""
api/routes/system.py

System information, control, and self-evolution endpoints.

This is the "nervous system" of the API layer:
  - Runtime info (OS, Python, version)
  - Component control (start / stop / restart individual subsystems)
  - Metrics snapshot
  - Self-evolution hooks: trigger re-planning, capability re-mapping,
    reflector analysis, and learning updates at runtime
  - Config hot-reload (no server restart needed)
  - Input mode switching (voice | panel | none) via ModeManager

All values are resolved dynamically — nothing is hardcoded.
"""

import logging
import platform
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException

logger = logging.getLogger("SystemRoute")

router = APIRouter(prefix="/api/system", tags=["system"])


# ─────────────────────────────────────────────────────────────────────────────
# Lazy helpers
# ─────────────────────────────────────────────────────────────────────────────

def _settings():
    from core.config import settings
    return settings


def _bus():
    from core.event_bus import bus
    return bus


def _orchestrator():
    from core.orchestrator import orchestrator
    return orchestrator


def _metrics():
    try:
        from core.metrics import metrics
        return metrics
    except Exception:
        return None


def _capability_mapper():
    try:
        from brain.capability_mapper import capability_mapper
        return capability_mapper
    except Exception:
        return None


def _reflector():
    try:
        from brain.reflector import reflector
        return reflector
    except Exception:
        return None


def _learner():
    try:
        from learning.learner import learner
        return learner
    except Exception:
        return None


def _lifecycle():
    try:
        from core.lifecycle_manager import lifecycle_manager
        return lifecycle_manager
    except Exception:
        return None


def _mode_manager():
    """Lazy import so mode_manager is never loaded before lifecycle boots it."""
    try:
        from core.mode_manager import mode_manager
        return mode_manager
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — Info
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/info")
async def system_info() -> Dict[str, Any]:
    """
    🖥️ Runtime environment information.

    Returns OS, Python version, project version, active config profile, etc.
    Nothing is hardcoded — all values come from platform / settings.
    """
    s = _settings()
    return {
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "os":             platform.system(),
        "os_version":     platform.version(),
        "machine":        platform.machine(),
        "python":         sys.version,
        "project":        getattr(s, "PROJECT_NAME",   "i_os_agent"),
        "version":        getattr(s, "VERSION",        "unknown"),
        "environment":    getattr(s, "ENVIRONMENT",    "development"),
        "llm_provider":   getattr(s, "LLM_PROVIDER",   "local"),
        "log_dir":        getattr(s, "LOG_DIR",        "logs"),
        "plugin_dir":     getattr(s, "PLUGIN_DIR",     "plugins"),
        "debug":          getattr(s, "DEBUG",          False),
    }


@router.get("/status")
async def system_status() -> Dict[str, Any]:
    """
    📊 Live system status: component states + active task count.
    """
    orch = _orchestrator()
    m    = _metrics()

    active_tasks   = len(getattr(orch, "active_tasks", {}))
    pending_tasks  = len(getattr(orch, "pending_tasks", []))
    completed      = getattr(m, "completed_tasks", 0) if m else 0
    failed         = getattr(m, "failed_tasks", 0)    if m else 0

    from api.routes.health import system_state

    return {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "overall":         "healthy" if system_state.event_bus_running else "degraded",
        "active_tasks":    active_tasks,
        "pending_tasks":   pending_tasks,
        "completed_tasks": completed,
        "failed_tasks":    failed,
        "components": {
            "event_bus":    "running" if system_state.event_bus_running    else "down",
            "orchestrator": "running" if system_state.orchestrator_running else "down",
            "executor":     "running" if system_state.executor_running     else "down",
        },
    }


@router.get("/metrics")
async def system_metrics() -> Dict[str, Any]:
    """
    📈 Runtime metrics snapshot.

    Falls back gracefully if the metrics module is not yet initialised.
    """
    m = _metrics()
    if m is None:
        return {"available": False, "message": "Metrics not initialised."}

    try:
        snapshot = m.snapshot() if hasattr(m, "snapshot") else vars(m)
        return {"available": True, "metrics": snapshot}
    except Exception as exc:
        logger.error("Metrics snapshot failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/config")
async def get_config() -> Dict[str, Any]:
    """
    ⚙️ Return the active configuration (sensitive keys redacted).
    """
    s = _settings()
    raw = {}
    for key in dir(s):
        if key.startswith("_"):
            continue
        val = getattr(s, key)
        if callable(val):
            continue
        # Redact anything that looks like a secret
        if any(kw in key.upper() for kw in ("KEY", "SECRET", "PASSWORD", "TOKEN")):
            val = "***REDACTED***"
        raw[key] = val
    return {"config": raw}


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — Control
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/config/reload")
async def reload_config() -> Dict[str, Any]:
    """
    🔄 Hot-reload configuration without restarting the server.

    Emits a 'config_reloaded' event so all subsystems can pick up changes.
    """
    try:
        s = _settings()
        if hasattr(s, "reload"):
            s.reload()

        await _bus().emit("config_reloaded", {}, source="api_system")
        return {"status": "reloaded", "timestamp": datetime.now(timezone.utc).isoformat()}

    except Exception as exc:
        logger.error("Config reload failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/component/{name}/restart")
async def restart_component(name: str) -> Dict[str, Any]:
    """
    🔁 Restart a named component at runtime.

    Supported component names are resolved dynamically from lifecycle_manager.
    """
    lm = _lifecycle()
    if lm is None:
        raise HTTPException(status_code=503, detail="LifecycleManager not available.")

    try:
        if not hasattr(lm, "restart_component"):
            raise HTTPException(status_code=501, detail="LifecycleManager does not support component restart.")
        result = await lm.restart_component(name)
        await _bus().emit("component_restarted", {"component": name}, source="api_system")
        return {"status": "restarted", "component": name, "detail": result}

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Component restart failed (%s): %s", name, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/shutdown")
async def request_shutdown() -> Dict[str, Any]:
    """
    ⛔ Request a graceful shutdown of the agent.

    Emits 'shutdown_requested' to the bus so lifecycle_manager can tear down
    components in the correct order.
    """
    try:
        await _bus().emit("shutdown_requested", {"source": "api"}, source="api_system")
        return {"status": "shutdown_requested"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — Self-Evolution
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/evolve/reflect")
async def trigger_reflection(
    context: Optional[Dict[str, Any]] = Body(default=None)
) -> Dict[str, Any]:
    """
    🧠 Trigger the Reflector to analyse recent behaviour and suggest improvements.
    """
    r = _reflector()
    if r is None:
        raise HTTPException(status_code=503, detail="Reflector not available.")

    try:
        result = await r.reflect(context or {})
        await _bus().emit("reflection_triggered", {"context": context}, source="api_system")
        return {"status": "reflected", "result": result}
    except Exception as exc:
        logger.error("Reflection failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/evolve/remap-capabilities")
async def remap_capabilities() -> Dict[str, Any]:
    """
    🗺️ Force a capability re-discovery pass.
    """
    cm = _capability_mapper()
    if cm is None:
        raise HTTPException(status_code=503, detail="CapabilityMapper not available.")

    try:
        result = await cm.remap() if hasattr(cm, "remap") else cm.build_map()
        await _bus().emit("capabilities_remapped", {}, source="api_system")
        return {"status": "remapped", "capabilities": result}
    except Exception as exc:
        logger.error("Capability remap failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/evolve/learn")
async def trigger_learning(
    payload: Optional[Dict[str, Any]] = Body(default=None)
) -> Dict[str, Any]:
    """
    📚 Trigger the Learner to consolidate recent experience into long-term memory.
    """
    l = _learner()
    if l is None:
        raise HTTPException(status_code=503, detail="Learner not available.")

    try:
        mode = (payload or {}).get("mode", "incremental")
        result = await l.learn(mode=mode)
        await _bus().emit("learning_triggered", {"mode": mode}, source="api_system")
        return {"status": "learning_complete", "mode": mode, "result": result}
    except Exception as exc:
        logger.error("Learning trigger failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/evolve/goal")
async def inject_goal(
    goal: Dict[str, Any] = Body(
        ...,
        examples=[{"goal": "Improve web search success rate by 20%", "priority": "high"}],
    )
) -> Dict[str, Any]:
    """
    🎯 Inject a high-level goal into the orchestrator's goal stack.
    """
    orch = _orchestrator()
    if orch is None:
        raise HTTPException(status_code=503, detail="Orchestrator not available.")

    description = goal.get("goal", "").strip()
    if not description:
        raise HTTPException(status_code=422, detail="'goal' field is required.")

    try:
        task_id = await orch.submit_goal(
            description=description,
            priority=goal.get("priority", "normal"),
            metadata={k: v for k, v in goal.items() if k not in ("goal", "priority")},
        )
        return {"status": "queued", "task_id": task_id, "goal": description}
    except Exception as exc:
        logger.error("Goal injection failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/evolve/history")
async def evolution_history(limit: int = 20) -> Dict[str, Any]:
    """
    📜 Return a history of self-evolution events.
    """
    try:
        from memory.episodic import episodic_memory
        events = await episodic_memory.query(
            filter_types=["reflection_complete", "capabilities_remapped", "learning_triggered"],
            limit=limit,
        )
        return {"history": events, "count": len(events)}
    except Exception as exc:
        logger.warning("Could not fetch evolution history: %s", exc)
        return {"history": [], "count": 0, "message": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — Input Mode
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/input-mode")
async def set_input_mode(
    payload: Dict[str, Any] = Body(
        ...,
        examples=[{"mode": "panel"}, {"mode": "voice"}, {"mode": "none"}],
    )
) -> Dict[str, Any]:
    """
    🔀 Switch the active input mode.

    Behaviour:
      • Validates the requested mode and transition are legal.
      • Waits for any in-flight orchestrator task to finish (action_completed)
        before switching — up to MODE_SWITCH_DRAIN_TIMEOUT seconds.
      • Tears down the outgoing subsystem, starts the incoming one.
      • Persists the new mode to .env so it survives restarts.
      • Publishes input_mode_changed on the EventBus — the WebSocket bridge
        forwards this to all connected dashboard clients automatically.

    Request body:
        {"mode": "voice" | "panel" | "none"}

    Response:
        {"mode": "panel", "previous_mode": "voice", "changed": true}
      or if already in that mode:
        {"mode": "panel", "changed": false, "reason": "already_active"}
    """
    mm = _mode_manager()
    if mm is None:
        raise HTTPException(
            status_code=503,
            detail="ModeManager not available — system may still be booting.",
        )

    raw_mode = payload.get("mode", "")
    if not raw_mode:
        raise HTTPException(status_code=422, detail="'mode' field is required.")

    # Validate and parse the raw string into an InputMode enum value.
    try:
        from core.input_mode import parse_mode
        new_mode = parse_mode(raw_mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Delegate all switching logic to ModeManager.
    try:
        from core.input_mode import ModeTransitionError
        result = await mm.set_mode(new_mode)
        return result

    except ModeTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    except Exception as exc:
        logger.error("Mode switch failed (%s): %s", raw_mode, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/input-mode")
async def get_input_mode() -> Dict[str, Any]:
    """
    🔀 Return the currently active input mode.

    Response:
        {"mode": "panel"}
    """
    mm = _mode_manager()
    if mm is None:
        # Fall back to reading CURRENT_MODE from settings if manager not yet ready.
        from core.config import settings
        return {"mode": getattr(settings, "CURRENT_MODE", "none")}

    return {"mode": mm.current_mode.value}