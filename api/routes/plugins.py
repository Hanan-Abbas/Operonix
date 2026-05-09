"""
api/routes/plugins.py

Plugin management endpoints — fully integrated with the self-evolution system.

Fixes from original:
  1. _generator() returned the module; now returns plugin_generator instance.
  2. /generate called generator.generate(spec) which didn't exist; now calls
     plugin_generator.generate(spec) which is the correct API method.
  3. Added /api/plugins/gaps endpoint to see detected capability gaps.
  4. Added /api/plugins/{name}/evolve to manually trigger plugin evolution.
  5. Consistent error handling across all endpoints.

Endpoints:
  GET    /api/plugins                → list all plugins
  GET    /api/plugins/gaps           → list detected capability gaps
  GET    /api/plugins/{name}         → get one plugin manifest
  POST   /api/plugins/{name}/enable
  POST   /api/plugins/{name}/disable
  POST   /api/plugins/{name}/reload
  POST   /api/plugins/{name}/evolve  → trigger evolution for a plugin
  POST   /api/plugins/reload-all
  POST   /api/plugins/generate       → generate a new plugin via AI
  DELETE /api/plugins/{name}         → unload and remove a plugin
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Path

logger = logging.getLogger("PluginsRoute")

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


# ── Lazy singletons ────────────────────────────────────────────────────────────
# Lazy imports prevent circular dependencies and allow the server to start
# even if the plugin system hasn't fully initialised yet.

def _loader():
    from plugins.loader import plugin_loader
    return plugin_loader


def _generator():
    # FIX: was importing the module and calling .generator on it
    # plugin_generator is the singleton instance at the bottom of generator.py
    from plugins.generator import plugin_generator
    return plugin_generator


def _gap_detector():
    from plugins.capability_gap_detector import capability_gap_detector
    return capability_gap_detector


def _evolver():
    from plugins.plugin_evolver import plugin_evolver
    return plugin_evolver


def _plugin_registry():
    from plugins.registry import plugin_registry
    return plugin_registry


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("")
async def list_plugins() -> Dict[str, Any]:
    """
    📦 List all discovered plugins with their status and capabilities.
    """
    try:
        plugins = _loader().list_plugins()
        return {
            "plugins": plugins,
            "count":   len(plugins),
            "summary": _plugin_registry().summary(),
        }
    except Exception as exc:
        logger.error("Failed to list plugins: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/gaps")
async def list_capability_gaps() -> Dict[str, Any]:
    """
    🔍 List intents that have triggered capability gap detection.

    Returns the consecutive failure counters and blocked intents so you
    can see what the agent is trying to learn.
    """
    try:
        detector = _gap_detector()
        return {
            "consecutive_failures": dict(detector._consecutive),
            "triggered_gaps":       {
                intent: {"last_triggered_at": ts}
                for intent, ts in detector._triggered.items()
            },
            "blocked_intents":      list(detector._blocked),
            "semantic_groups":      detector._groups,
        }
    except Exception as exc:
        logger.error("Failed to list capability gaps: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{name}")
async def get_plugin(name: str = Path(..., description="Plugin name")) -> Dict[str, Any]:
    """📄 Get full manifest and runtime stats for one plugin."""
    try:
        plugin = _loader().get_plugin(name)
        if plugin is None:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")
        return plugin
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{name}/enable")
async def enable_plugin(name: str = Path(...)) -> Dict[str, Any]:
    """✅ Enable a plugin (set status to TRUSTED)."""
    try:
        result = await _loader().enable(name)
        return {"status": "enabled", "plugin": name, "detail": result}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{name}/disable")
async def disable_plugin(name: str = Path(...)) -> Dict[str, Any]:
    """🚫 Disable a plugin without removing it from disk."""
    try:
        result = await _loader().disable(name)
        return {"status": "disabled", "plugin": name, "detail": result}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{name}/reload")
async def reload_plugin(name: str = Path(...)) -> Dict[str, Any]:
    """🔄 Hot-reload a single plugin without restarting the server."""
    try:
        result = await _loader().reload(name)
        return {"status": "reloaded", "plugin": name, "detail": result}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{name}/evolve")
async def evolve_plugin(
    name: str = Path(...),
    body: Dict[str, Any] = Body(default={}),
) -> Dict[str, Any]:
    """
    🧬 Manually trigger evolution for a specific plugin.

    Evolution re-generates the plugin code using the LLM based on its
    failure history, then validates and deploys the improved version.

    Body (optional):
        reason – why you're requesting evolution
    """
    try:
        entry = _plugin_registry().get(name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")

        intent = entry.manifest.intent or name
        reason = body.get("reason", "Manual evolution request via API")

        from core.event_bus import bus
        bus.publish(
            "plugin_evolution_requested",
            {"name": name, "intent": intent, "reason": reason},
            source="plugins_route",
        )
        return {
            "status":  "evolution_triggered",
            "plugin":  name,
            "intent":  intent,
            "reason":  reason,
            "message": f"Evolution started for '{name}'. Check logs for progress.",
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/reload-all")
async def reload_all_plugins() -> Dict[str, Any]:
    """🔄 Hot-reload ALL currently registered plugins."""
    try:
        results = await _loader().reload_all()
        return {"status": "reloaded_all", "results": results}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/generate")
async def generate_plugin(
    spec: Dict[str, Any] = Body(
        ...,
        examples={
            "weather_lookup": {
                "summary": "Weather lookup plugin",
                "value": {
                    "name":         "weather_lookup",
                    "description":  "Fetches real-time weather for a given city",
                    "intent":       "weather_lookup",
                    "capabilities": ["web", "api"],
                    "parameters":   {"city": "str"},
                },
            }
        },
    ),
) -> Dict[str, Any]:
    """
    🤖 Generate a new plugin using the AI self-evolution engine.

    The generator uses the LLM to write plugin.py + manifest.json,
    then runs sandbox validation (LLM audit → sandbox exec → pytest).
    Low-risk plugins are auto-deployed. Others require approval.

    Body:
        name        – snake_case capability name (required)
        description – what the plugin should do (natural language)
        intent      – intent string (defaults to name)
        capabilities – list of capability tags
        parameters  – dict of param_name → type hint string
    """
    name = spec.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="'name' is required.")

    try:
        # FIX: was calling _generator().generate(spec) on wrong object.
        # Now correctly calls plugin_generator.generate(spec) which is
        # the API method added to PluginGenerator.
        result = await _generator().generate(spec)
        return {
            "status":  "generated" if result.get("success") else "failed",
            "plugin":  result.get("plugin", name),
            "intent":  result.get("intent", name),
            "files":   result.get("files", []),
            "message": result.get("message", ""),
        }
    except Exception as exc:
        logger.error("Plugin generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/approve/{name}")
async def approve_plugin(name: str = Path(...)) -> Dict[str, Any]:
    """
    ✅ Approve a PENDING plugin and deploy it.

    Called by the dashboard when a user reviews and approves a
    generated plugin that requires confirmation.
    """
    try:
        entry = _plugin_registry().get(name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")

        plugin_dir = entry.plugin_dir
        intent     = entry.manifest.intent or name

        from core.event_bus import bus
        bus.publish(
            "plugin_approved",
            {"name": name, "intent": intent, "plugin_dir": plugin_dir},
            source="plugins_route",
        )
        return {
            "status":  "approved",
            "plugin":  name,
            "message": f"Plugin '{name}' approved and deploying.",
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{name}")
async def delete_plugin(name: str = Path(...)) -> Dict[str, Any]:
    """
    🗑️ Unload and permanently delete a plugin from disk.
    This is irreversible.
    """
    try:
        result = await _loader().remove(name)
        return {"status": "removed", "plugin": name, "detail": result}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))