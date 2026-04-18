"""
api/routes/plugins.py

Plugin management endpoints.

Delegates all heavy lifting to plugins/loader.py and plugins/generator.py —
this layer is purely an HTTP facade.  No plugin logic is hardcoded here.

Endpoints:
  GET    /api/plugins            → list all discovered plugins
  GET    /api/plugins/{name}     → get one plugin's manifest
  POST   /api/plugins/{name}/enable
  POST   /api/plugins/{name}/disable
  POST   /api/plugins/{name}/reload
  POST   /api/plugins/reload-all
  POST   /api/plugins/generate   → ask the generator to scaffold a new plugin
  DELETE /api/plugins/{name}     → unload & remove a plugin
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Path

logger = logging.getLogger("PluginsRoute")

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


# ─────────────────────────────────────────────────────────────────────────────
# Lazy imports — avoids circular deps and lets the server start even if the
# plugin system hasn't fully initialised yet.
# ─────────────────────────────────────────────────────────────────────────────

def _loader():
    from plugins.loader import loader
    return loader


def _generator():
    from plugins.generator import generator
    return generator


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("")
async def list_plugins() -> Dict[str, Any]:
    """
    📦 List all discovered plugins with their status.

    Returns:
        {
            "plugins": [
                {
                    "name": "web_search",
                    "version": "1.0.0",
                    "enabled": true,
                    "loaded": true,
                    "description": "...",
                    "capabilities": [...]
                }, ...
            ],
            "count": N
        }
    """
    try:
        plugins = _loader().list_plugins()
        return {"plugins": plugins, "count": len(plugins)}
    except Exception as exc:
        logger.error("Failed to list plugins: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{name}")
async def get_plugin(name: str = Path(..., description="Plugin name")) -> Dict[str, Any]:
    """📄 Get full manifest and status for one plugin."""
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
    """✅ Enable a plugin (persists across restarts)."""
    try:
        result = await _loader().enable(name)
        return {"status": "enabled", "plugin": name, "detail": result}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{name}/disable")
async def disable_plugin(name: str = Path(...)) -> Dict[str, Any]:
    """🚫 Disable a plugin without removing it."""
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


@router.post("/reload-all")
async def reload_all_plugins() -> Dict[str, Any]:
    """🔄 Hot-reload ALL enabled plugins."""
    try:
        results = await _loader().reload_all()
        return {"status": "reloaded_all", "results": results}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/generate")
async def generate_plugin(
    spec: Dict[str, Any] = Body(
        ...,
        example={
            "name":        "weather_lookup",
            "description": "Fetches real-time weather for a given city",
            "capabilities": ["web", "api"],
            "parameters":  {"city": "str"},
        },
    )
) -> Dict[str, Any]:
    """
    🤖 Scaffold a new plugin using the AI generator.

    The generator (plugins/generator.py) uses the LLM to write manifest.json
    and the plugin module, then saves them to the plugins/ directory.

    Body:
        name         – snake_case plugin identifier
        description  – what the plugin should do (natural language)
        capabilities – list of capability tags (optional)
        parameters   – dict of param_name → type hint string (optional)
    """
    name = spec.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="'name' is required.")

    try:
        result = await _generator().generate(spec)
        return {
            "status":  "generated",
            "plugin":  name,
            "files":   result.get("files", []),
            "message": result.get("message", "Plugin scaffolded successfully."),
        }
    except Exception as exc:
        logger.error("Plugin generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{name}")
async def delete_plugin(name: str = Path(...)) -> Dict[str, Any]:
    """
    🗑️ Unload and permanently remove a plugin.

    This is irreversible. The plugin directory is deleted from disk.
    """
    try:
        result = await _loader().remove(name)
        return {"status": "removed", "plugin": name, "detail": result}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))