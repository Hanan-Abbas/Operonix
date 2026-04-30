"""
core/mode_manager.py — Operonix AI OS Agent
════════════════════════════════════════════
Owns all input-mode switching logic.

"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Optional

from core.input_mode import ALLOWED_TRANSITIONS, InputMode, ModeTransitionError, parse_mode

logger = logging.getLogger("ModeManager")

_ENV_PATH: Path = Path(__file__).resolve().parent.parent / ".env"
_TASK_DRAIN_TIMEOUT: float = float(os.getenv("MODE_SWITCH_DRAIN_TIMEOUT", "30.0"))


class ModeManager:
    """Central controller for voice ↔ panel ↔ none switching."""

    def __init__(self) -> None:
        raw = os.getenv("CURRENT_MODE", InputMode.PANEL.value)
        try:
            self._current_mode: InputMode = parse_mode(raw)
        except ValueError:
            logger.warning("ModeManager: CURRENT_MODE=%r invalid. Defaulting to PANEL.", raw)
            self._current_mode = InputMode.PANEL

        self._lock = asyncio.Lock()
        self._task_done_event: Optional[asyncio.Event] = None
        self._orchestrator: Optional[object] = None

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def current_mode(self) -> InputMode:
        return self._current_mode

    def initialise(self, orchestrator: object) -> None:
        """Called by lifecycle_manager after orchestrator.start()."""
        from core.event_bus import bus

        self._orchestrator = orchestrator

        bus.subscribe("action_completed", self._on_action_completed)

        bus.subscribe("panel_mode_switch_requested", self._on_panel_mode_switch)

        logger.info("ModeManager: initialised. Boot mode = %s.", self._current_mode.name)

        from core.event_bus import bus as _bus
        _bus.publish(
            "input_mode_initialised",
            {"mode": self._current_mode.value},
            source="mode_manager",
        )
        asyncio.get_event_loop().create_task(self._apply_mode(self._current_mode))

    async def set_mode(self, new_mode: InputMode) -> dict:
        """
        Public entry point for mode switching.
        Called by api/routes/system.py and by _on_panel_mode_switch().
        """
        if new_mode == self._current_mode:
            return {"mode": new_mode.value, "changed": False, "reason": "already_active"}

        if new_mode not in ALLOWED_TRANSITIONS.get(self._current_mode, set()):
            raise ModeTransitionError(self._current_mode, new_mode)

        async with self._lock:
            previous_mode = self._current_mode

            await self._drain_active_tasks()

            logger.info("ModeManager: switching %s → %s …", previous_mode.name, new_mode.name)

            await self._teardown(previous_mode, new_mode)
            await self._startup(new_mode)

            self._current_mode = new_mode
            self._persist_to_env(new_mode)

            from core.event_bus import bus
            bus.publish(
                "input_mode_changed",
                {"new_mode": new_mode.value, "previous_mode": previous_mode.value},
                source="mode_manager",
            )

            logger.info("ModeManager: now in %s mode.", new_mode.name)
            return {
                "mode":          new_mode.value,
                "previous_mode": previous_mode.value,
                "changed":       True,
            }

    # ── Panel mode switch event handler ──────────────────────────────────────

    async def _on_panel_mode_switch(self, event: object) -> None:
        """
        High-priority EventBus handler for panel UI mode button clicks.

        Published by panel_controller._on_mode_change_requested() when the
        user clicks a mode button in the panel. Delegates to set_mode() which
        handles validation, drain, teardown, startup, persistence, and notify.
        """
        payload = event.data if hasattr(event, "data") else event  # type: ignore[union-attr]
        if not isinstance(payload, dict):
            return

        raw_mode = payload.get("mode", "")
        if not raw_mode:
            return

        try:
            new_mode = parse_mode(raw_mode)
            await self.set_mode(new_mode)
        except ModeTransitionError as exc:
            logger.warning("ModeManager: panel mode switch blocked — %s", exc)
        except Exception as exc:
            logger.error("ModeManager: panel mode switch failed — %s", exc)

    # ── Task drain ────────────────────────────────────────────────────────────

    async def _drain_active_tasks(self) -> None:
        orch = self._orchestrator
        if orch is None:
            return

        active: dict = getattr(orch, "active_tasks", {})
        if not active:
            return

        logger.info(
            "ModeManager: %d task(s) in flight — waiting up to %.0fs …",
            len(active), _TASK_DRAIN_TIMEOUT,
        )
        self._task_done_event = asyncio.Event()
        try:
            await asyncio.wait_for(self._wait_for_tasks_empty(), timeout=_TASK_DRAIN_TIMEOUT)
            logger.info("ModeManager: all tasks finished.")
        except asyncio.TimeoutError:
            logger.warning("ModeManager: drain timeout. Switching anyway.")
        finally:
            self._task_done_event = None

    async def _wait_for_tasks_empty(self) -> None:
        orch = self._orchestrator
        while True:
            if not getattr(orch, "active_tasks", {}):
                return
            if self._task_done_event:
                self._task_done_event.clear()
                try:
                    await asyncio.wait_for(self._task_done_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass

    async def _on_action_completed(self, event: object) -> None:
        if self._task_done_event is not None:
            self._task_done_event.set()

    # ── Teardown ──────────────────────────────────────────────────────────────

    async def _teardown(self, mode: InputMode, new_mode: InputMode) -> None:
        if mode == InputMode.VOICE:
            await self._teardown_voice(new_mode)
        elif mode == InputMode.PANEL:
            await self._teardown_panel()

    async def _teardown_voice(self, new_mode: InputMode) -> None:
        """
        Full voice handoff sequence:
          1. pipeline.request_stop() — exit capture loop after current utterance
          2. pipeline.flush_tail()   — wait for pipeline to finish + drain buffer
          3. audio_manager.stop()    — release mic hardware (OS indicator off)
          4. Publish system_mode_changed so panel/dashboard/models can react
        """
        orch = self._orchestrator
        if orch is None:
            return

        logger.info("ModeManager: tearing down VOICE subsystem …")
        orch._voice_active = False
        # 1. Signal pipeline to stop gracefully after finishing the utterance.
        pipeline = getattr(orch, "pipeline", None)
        if pipeline is not None:
            try:
                pipeline.request_stop()
            except Exception as exc:
                logger.warning("ModeManager: pipeline.request_stop() error — %s", exc)

        # 2. Flush — wait for current utterance + drain hardware buffer.
        #    Run in executor because flush_tail() blocks (threading.Event.wait).
        if pipeline is not None:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, pipeline.flush_tail)
                logger.info("ModeManager: pipeline flush complete.")
            except Exception as exc:
                logger.warning("ModeManager: pipeline.flush_tail() error — %s", exc)

        # 3. Stop the mic hardware — releases device lock, turns off OS indicator.
        orch._voice_active = False  # type: ignore[attr-defined]
        audio_manager = getattr(orch, "audio_manager", None)
        if audio_manager is not None:
            loop = asyncio.get_running_loop()
            logger.info("ModeManager: AudioManager stopped — mic hardware released.")

        # 4. Notify all subsystems that voice mode has been torn down.
        from core.event_bus import bus
        bus.publish(
            "system_mode_changed",
            {"from_mode": "voice", "to_mode": new_mode.value},  # ← correct
            source="mode_manager",
        )

        logger.info("ModeManager: VOICE teardown complete.")

    async def _teardown_panel(self) -> None:
        """Stop the panel via orchestrator._stop_panel() (Qt-safe unified path)."""
        orch = self._orchestrator
        if orch is None:
            return

        panel_thread = getattr(orch, "_panel_thread", None)
        if panel_thread is None or not panel_thread.is_alive():
            logger.info("ModeManager: no panel thread running — skipping teardown.")
            return

        logger.info("ModeManager: tearing down PANEL subsystem …")
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, orch._stop_panel)
        except Exception as exc:
            logger.warning("ModeManager: panel stop error — %s", exc)

        logger.info("ModeManager: PANEL teardown complete.")

    # ── Startup ───────────────────────────────────────────────────────────────

    async def _startup(self, mode: InputMode) -> None:
        if mode == InputMode.VOICE:
            await self._startup_voice()
        elif mode == InputMode.PANEL:
            await self._startup_panel()

    async def _startup_voice(self) -> None:
        """
        Voice startup sequence:
          1. Start audio_manager (open mic hardware).
          2. pipeline.reset() — clear stop flag, fresh VAD state.
          3. Enable wake-word loop gate.
          4. Publish system_mode_changed.
        """
        orch = self._orchestrator
        if orch is None:
            return

        logger.info("ModeManager: starting VOICE subsystem …")

        # 1. Open mic hardware.
        audio_manager = getattr(orch, "audio_manager", None)
        if audio_manager is not None:
            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(None, audio_manager.start)
            if not success:
                logger.error("ModeManager: AudioManager failed to start — VOICE may not work.")
            else:
                logger.info("ModeManager: AudioManager started — mic is LIVE.")

        # 2. Reset pipeline state for a fresh session.
        pipeline = getattr(orch, "pipeline", None)
        if pipeline is not None:
            try:
                pipeline.reset()
            except Exception as exc:
                logger.warning("ModeManager: pipeline.reset() error — %s", exc)

        # 3. Enable wake-word loop.
        orch._voice_active = True  # type: ignore[attr-defined]

        # 4. Notify subsystems.
        from core.event_bus import bus
        bus.publish(
            "system_mode_changed",
            {"from_mode": self._current_mode.value, "to_mode": "voice"},
            source="mode_manager",
        )

        logger.info("ModeManager: VOICE startup complete.")

    async def _startup_panel(self) -> None:
        """
        Panel startup sequence:
          1. Ensure mic is stopped (in case called at boot with auto_start=False,
             or after a failed voice teardown — idempotent).
          2. Start the panel Qt thread via orchestrator._start_panel().
          3. Publish system_mode_changed so the panel can auto-focus the input box.
        """
        orch = self._orchestrator
        if orch is None:
            return

        logger.info("ModeManager: starting PANEL subsystem …")

        # 1. Ensure mic is not open — idempotent (stop() is a no-op if not running).
        audio_manager = getattr(orch, "audio_manager", None)
        if audio_manager is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, audio_manager.stop)
            logger.info("ModeManager: mic confirmed stopped before panel start.")

        # 2. Start the panel (re-entry guard in _start_panel prevents double panel).
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, orch._start_panel)
            logger.info("ModeManager: PanelController started.")
        except Exception as exc:
            logger.error("ModeManager: panel startup error — %s", exc)

        # 3. Notify — panel_controller listens for this to auto-focus the input box.
        from core.event_bus import bus
        bus.publish(
            "system_mode_changed",
            {"from_mode": self._current_mode.value, "to_mode": "panel"},
            source="mode_manager",
        )

        logger.info("ModeManager: PANEL startup complete.")

    # ── Apply mode at boot ────────────────────────────────────────────────────

    async def _apply_mode(self, mode: InputMode) -> None:
        logger.info("ModeManager: applying boot mode %s …", mode.name)
        await self._startup(mode)
        logger.info("ModeManager: boot mode %s applied.", mode.name)

    # ── .env persistence ──────────────────────────────────────────────────────

    def _persist_to_env(self, mode: InputMode) -> None:
        key = "CURRENT_MODE"
        new_line = f"{key}={mode.value}\n"
        try:
            if _ENV_PATH.exists():
                original = _ENV_PATH.read_text(encoding="utf-8")
                pattern = re.compile(rf"^{key}\s*=.*$", re.MULTILINE)
                if pattern.search(original):
                    updated = pattern.sub(new_line.rstrip(), original)
                else:
                    updated = original.rstrip("\n") + "\n" + new_line
                _ENV_PATH.write_text(updated, encoding="utf-8")
            else:
                _ENV_PATH.write_text(new_line, encoding="utf-8")
            logger.info("ModeManager: persisted %s=%s to .env", key, mode.value)
        except OSError as exc:
            logger.warning("ModeManager: could not write to .env — %s", exc)


# ── Singleton ─────────────────────────────────────────────────────────────────
mode_manager = ModeManager()