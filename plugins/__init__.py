"""
plugins/__init__.py

Plugin system package.

Exports the key components and provides a single start() coroutine
that the orchestrator can call at agent boot to bring up the entire
plugin subsystem in the correct order.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("PluginSystem")


async def start_plugin_system():
    """
    Initialises the complete plugin subsystem in dependency order.

    Call this from core/orchestrator.py or core/lifecycle_manager.py
    after capabilities and memory have been initialised.

    Order:
      1. plugin_memory     — needs DB, no dependencies
      2. plugin_registry   — in-memory, no dependencies
      3. plugin_loader     — scans installed/, needs registry
      4. gap_detector      — needs episodic_memory (already started)
      5. generator         — needs gap_detector events
      6. evolver           — needs generator events
      7. health_monitor    — needs registry + memory
    """
    from plugins.plugin_memory           import plugin_memory
    from plugins.registry                import plugin_registry
    from plugins.loader                  import plugin_loader
    from plugins.capability_gap_detector import capability_gap_detector
    from plugins.generator               import plugin_generator
    from plugins.plugin_evolver          import plugin_evolver
    from plugins.plugin_health_monitor   import plugin_health_monitor

    await plugin_memory.start()
    await plugin_loader.start()
    await capability_gap_detector.start()
    await plugin_generator.start()
    await plugin_evolver.start()
    await plugin_health_monitor.start()

    summary = plugin_registry.summary()
    logger.info(
        f"🔌 Plugin system online. "
        f"Plugins: {summary['total']} total, "
        f"{summary['trusted']} trusted, "
        f"{summary['pending']} pending."
    )


__all__ = [
    "start_plugin_system",
]