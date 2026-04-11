# api/routes/health.py
"""
🏥 Health check and diagnostics endpoints.
Helps monitor system status and debug issues.
"""

from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException
import psutil
import logging

logger = logging.getLogger("HealthCheck")

router = APIRouter(prefix="/api", tags=["health"])


# ============================================================================
# Global System State (populated by lifecycle_manager)
# ============================================================================

class SystemState:
    """Shared system state for health checks."""
    event_bus_running: bool = False
    orchestrator_running: bool = False
    executor_running: bool = False
    audio_manager: Optional[Any] = None
    stt_model: Optional[Any] = None
    llm_client: Optional[Any] = None
    startup_time: datetime = datetime.now()


system_state = SystemState()


# ============================================================================
# Health Check Endpoints
# ============================================================================

@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """
    🏥 Basic health check.
    
    Returns:
        {
            "status": "healthy" | "degraded" | "unhealthy",
            "uptime_seconds": float,
            "components": {
                "event_bus": "running" | "down",
                "orchestrator": "running" | "down",
                "executor": "running" | "down",
                "stt_model": "loaded" | "not_loaded",
                "llm_client": "ready" | "not_ready"
            }
        }
    """
    uptime = (datetime.now() - system_state.startup_time).total_seconds()
    
    status = "healthy"
    
    # Count down components
    down_count = sum([
        not system_state.event_bus_running,
        not system_state.orchestrator_running,
        not system_state.executor_running,
        system_state.stt_model is None,
    ])
    
    if down_count >= 2:
        status = "unhealthy"
    elif down_count >= 1:
        status = "degraded"
    
    return {
        "status": status,
        "uptime_seconds": uptime,
        "timestamp": datetime.now().isoformat(),
        "components": {
            "event_bus": "running" if system_state.event_bus_running else "down",
            "orchestrator": "running" if system_state.orchestrator_running else "down",
            "executor": "running" if system_state.executor_running else "down",
            "stt_model": "loaded" if system_state.stt_model is not None else "not_loaded",
            "llm_client": "ready" if system_state.llm_client is not None else "not_ready",
        }
    }


@router.get("/health/detailed")
async def health_detailed() -> Dict[str, Any]:
    """
    🔍 Detailed system diagnostics.
    
    Returns comprehensive information about CPU, memory, audio, etc.
    """
    try:
        # CPU & Memory
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        
        audio_info = {}
        if system_state.audio_manager:
            audio_info = system_state.audio_manager.device_info()
        
        return {
            "status": "detailed_report",
            "timestamp": datetime.now().isoformat(),
            
            # System Resources
            "system": {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "memory_gb": {
                    "total": round(memory.total / (1024**3), 2),
                    "used": round(memory.used / (1024**3), 2),
                    "available": round(memory.available / (1024**3), 2),
                }
            },
            
            # Audio System
            "audio": audio_info,
            
            # Core Components
            "components": {
                "event_bus": "running" if system_state.event_bus_running else "down",
                "orchestrator": "running" if system_state.orchestrator_running else "down",
                "executor": "running" if system_state.executor_running else "down",
                "stt_model": {
                    "loaded": system_state.stt_model is not None,
                    "size": getattr(system_state.stt_model, "model_size", "unknown")
                    if system_state.stt_model else None,
                },
                "llm_client": {
                    "ready": system_state.llm_client is not None,
                }
            },
            
            # Uptime
            "uptime_seconds": (datetime.now() - system_state.startup_time).total_seconds(),
        }
        
    except Exception as e:
        logger.error(f"Failed to collect diagnostics: {e}")
        raise HTTPException(status_code=500, detail=f"Diagnostics failed: {e}")


@router.get("/health/audio")
async def audio_diagnostics() -> Dict[str, Any]:
    """
    🎙️ Audio system diagnostics.
    
    Returns:
        Audio device info, sample rate, channels, etc.
    """
    if not system_state.audio_manager:
        return {
            "status": "not_available",
            "message": "AudioManager not initialized"
        }
    
    try:
        info = system_state.audio_manager.device_info()
        
        return {
            "status": "available",
            "device": info,
            "is_running": system_state.audio_manager.is_running,
            "overflow_count": system_state.audio_manager.overflow_count,
        }
        
    except Exception as e:
        logger.error(f"Audio diagnostics failed: {e}")
        raise HTTPException(status_code=500, detail=f"Audio check failed: {e}")


@router.get("/health/stt")
async def stt_diagnostics() -> Dict[str, Any]:
    """
    🗣️ Speech-to-Text model diagnostics.
    
    Returns model info and readiness status.
    """
    if not system_state.stt_model:
        return {
            "status": "not_loaded",
            "message": "STT model not initialized"
        }
    
    try:
        model_info = {
            "model_size": getattr(system_state.stt_model, "model_size", "unknown"),
            "device": getattr(system_state.stt_model, "device", "unknown"),
            "compute_type": getattr(system_state.stt_model, "compute_type", "unknown"),
            "beam_size": getattr(system_state.stt_model, "beam_size", 5),
            "language": getattr(system_state.stt_model, "language", "en"),
        }
        
        return {
            "status": "loaded",
            "model": model_info,
            "ready": True,
        }
        
    except Exception as e:
        logger.error(f"STT diagnostics failed: {e}")
        raise HTTPException(status_code=500, detail=f"STT check failed: {e}")


@router.get("/health/llm")
async def llm_diagnostics() -> Dict[str, Any]:
    """
    🧠 Large Language Model diagnostics.
    
    Returns LLM provider info and readiness.
    """
    if not system_state.llm_client:
        return {
            "status": "not_available",
            "message": "LLM Client not initialized"
        }
    
    try:
        from core.config import settings
        
        return {
            "status": "available",
            "providers": {
                "deepseek": {
                    "configured": bool(getattr(settings, "DEEPSEEK_API_KEY", None)),
                },
                "gemini": {
                    "configured": bool(getattr(settings, "GEMINI_API_KEY", None)),
                },
                "openai": {
                    "configured": bool(getattr(settings, "OPENAI_API_KEY", None)),
                },
                "local": {
                    "configured": True,  # Ollama is always available
                }
            },
            "primary": getattr(settings, "LLM_PROVIDER", "local"),
        }
        
    except Exception as e:
        logger.error(f"LLM diagnostics failed: {e}")
        raise HTTPException(status_code=500, detail=f"LLM check failed: {e}")


@router.post("/health/test-audio")
async def test_audio_capture() -> Dict[str, Any]:
    """
    🎙️ Test audio capture (blocking for up to 5 seconds).
    
    Attempts to capture a short audio sample.
    
    Returns:
        {
            "success": bool,
            "duration_seconds": float,
            "frames_captured": int,
            "message": str
        }
    """
    if not system_state.audio_manager:
        return {
            "success": False,
            "message": "AudioManager not initialized"
        }
    
    try:
        import asyncio
        import numpy as np
        
        # Capture 2 seconds of audio
        frames = []
        for _ in range(int(2 * 16000 / 512)):  # ~2 seconds @ 512 chunk size
            chunk = system_state.audio_manager.read_chunk()
            if chunk is not None:
                frames.append(chunk)
            await asyncio.sleep(0.01)
        
        if not frames:
            return {
                "success": False,
                "duration_seconds": 0,
                "frames_captured": 0,
                "message": "Failed to capture audio"
            }
        
        audio = np.concatenate(frames)
        rms = float(np.sqrt(np.mean(audio.astype(np.float32)**2)))
        
        return {
            "success": True,
            "duration_seconds": len(frames) * 512 / 16000,
            "frames_captured": len(frames),
            "rms_level": float(rms),
            "message": f"Captured {len(frames)} frames ({rms:.0f} RMS)"
        }
        
    except Exception as e:
        logger.error(f"Audio test failed: {e}")
        return {
            "success": False,
            "message": f"Audio test failed: {e}"
        }


# ============================================================================
# Status Summary Endpoint
# ============================================================================

@router.get("/status")
async def status_summary() -> Dict[str, Any]:
    """
    📊 Quick status summary (includes active tasks, metrics).
    
    Returns:
        {
            "overall_status": "healthy" | "degraded" | "unhealthy",
            "active_tasks": int,
            "uptime_seconds": float,
            "components": {...}
        }
    """
    from core.orchestrator import orchestrator
    
    uptime = (datetime.now() - system_state.startup_time).total_seconds()
    
    return {
        "overall_status": "healthy" if system_state.event_bus_running else "unhealthy",
        "timestamp": datetime.now().isoformat(),
        "uptime_seconds": uptime,
        "active_tasks": len(orchestrator.active_tasks),
        "components": {
            "event_bus": "running" if system_state.event_bus_running else "down",
            "orchestrator": "running" if system_state.orchestrator_running else "down",
            "executor": "running" if system_state.executor_running else "down",
        }
    }