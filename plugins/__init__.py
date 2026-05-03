"""
plugins/__init__.py

Boots the complete self-evolution pipeline in the correct order:

  1. plugin_loader       — loads existing installed plugins from disk
  2. plugin_evolver      — upgrades degrading plugins
  3. plugin_generator    — generates new plugins for capability gaps
  4. capability_gap_detector — monitors failures and fires gap events

Order matters:
  - loader must run before gap_detector so existing plugins don't get
    re-generated on every restart
  - generator must subscribe before gap_detector fires its first event
  - evolver must be ready before any plugin_evolution_requested fires

Self-evolution loop:
  unknown intent
    → mapping_failed event
    → capability_gap_detector._on_mapping_failed (immediate trigger)
    → capability_gap_detected event
    → plugin_generator._on_gap_detected
    → LLM generates plugin code + tests
    → sandbox_runner validates (LLM audit → sandbox exec → pytest)
    → auto-approve if low-risk OR prompt user for medium/high
    → plugin_loader.hot_reload
    → capability_registry.register (plugin now handles the intent)
    → next request for same intent → plugin executes ✓
"""
from __future__ import annotations

import logging

logger = logging.getLogger("PluginSystem")


async def start_plugin_system() -> None:
    """
    Boot the complete self-evolving plugin system.
    Called once by lifecycle_manager.startup() after the orchestrator starts.
    """

    # ── 1. Plugin Loader — load installed plugins from disk ────────────────
    from plugins.loader import plugin_loader
    await plugin_loader.start()

    # ── 2. Plugin Evolver — upgrade degrading plugins ──────────────────────
    try:
        from plugins.plugin_evolver import plugin_evolver
        await plugin_evolver.start()
        logger.info("🧬 Plugin Evolver: Online.")
    except Exception as exc:
        logger.warning("Plugin Evolver could not start: %s", exc)

    # ── 3. Plugin Generator — generate new plugins for capability gaps ──────
    try:
        from plugins.generator import plugin_generator
        await plugin_generator.start()
        logger.info("🏭 Plugin Generator: Online.")
    except Exception as exc:
        logger.warning("Plugin Generator could not start: %s", exc)

    # ── 4. Capability Gap Detector — must start LAST so generator is ───────
    #       already subscribed before the first gap event fires
    try:
        from plugins.capability_gap_detector import capability_gap_detector
        await capability_gap_detector.start()
        logger.info("🔎 Capability Gap Detector: Online.")
    except Exception as exc:
        logger.warning("Capability Gap Detector could not start: %s", exc)

    # ── 5. Plugin Health Monitor (optional) ───────────────────────────────
    try:
        from plugins.plugin_health_monitor import plugin_health_monitor
        await plugin_health_monitor.start()
        logger.info("💊 Plugin Health Monitor: Online.")
    except Exception as exc:
        logger.debug("Plugin Health Monitor not available: %s", exc)

    logger.info(
        "✅ Plugin system fully operational. "
        "Agent will now self-evolve when new intents are detected."
    )