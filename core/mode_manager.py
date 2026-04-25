"""
core/mode_manager.py — Operonix AI OS Agent
════════════════════════════════════════════
Owns all input-mode switching logic.

Responsibilities:
  • Holds the single authoritative CURRENT_MODE at runtime
  • Validates transitions against ALLOWED_TRANSITIONS
  • Waits for any active orchestrator task to finish before switching
    (listens for action_completed on the EventBus)
  • Tears down the outgoing subsystem, starts the incoming one
  • Persists the new mode to .env so it survives restarts
  • Publishes input_mode_changed after every successful switch

Design rules:
  • Never imports from voice/ or panel/ at module level — all subsystem
    references are lazy inside _teardown_voice / _startup_voice etc.
    This avoids circular imports and lets the module load cleanly before
    those subsystems exist.
  • Does NOT touch orchestrator internals directly — it flips flags that
    the orchestrator's own loops check (orchestrator._voice_active).
  • Thread-safe: asyncio.Lock guards the switch sequence so two rapid
    API calls cannot interleave teardown/startup steps.

FIX CHANGELOG (Step 1):
  • _startup_panel() now delegates entirely to orchestrator._start_panel()
    which already contains a re-entry guard (checks if the panel thread is
    alive before spawning a new one). This means switching panel → panel
    or calling _startup_panel() while the thread is running is a safe no-op.
  • _teardown_panel() now calls orchestrator._stop_panel() (the new unified
    stop helper) instead of calling panel_ctrl.stop() directly via an
    executor. _stop_panel() handles both controller teardown and Qt thread
    joining in the correct order.
  • _apply_mode() at boot is unchanged — it calls _startup(mode) which
    calls _startup_panel() → _start_panel(). Because orchestrator.start()
    no longer calls _start_panel() itself, this is now the single boot-time
    panel creation path. No double panel.
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

# Path to the project-root .env file.
# BASE_DIR is two levels up from this file (core/mode_manager.py → core/ → project/).
_ENV_PATH: Path = Path(__file__).resolve().parent.parent / ".env"

# How long (seconds) to wait for a running task to emit action_completed
# before giving up and switching anyway.
_TASK_DRAIN_TIMEOUT: float = float(os.getenv("MODE_SWITCH_DRAIN_TIMEOUT", "30.0"))


class ModeManager:
    """
    Central controller for voice ↔ panel ↔ none switching.

    Instantiated once as `mode_manager` at the bottom of this file.
    Wired into the system by lifecycle_manager.startup() which calls
    mode_manager.initialise(orchestrator) after the orchestrator starts.
    """

    def __init__(self) -> None:
        # Read starting mode from .env; default to PANEL per product decision.
        raw = os.getenv("CURRENT_MODE", InputMode.PANEL.value)
        try:
            self._current_mode: InputMode = parse_mode(raw)
        except ValueError:
            logger.warning(
                "ModeManager: CURRENT_MODE=%r in .env is invalid. Defaulting to PANEL.", raw
            )
            self._current_mode = InputMode.PANEL

        self._lock = asyncio.Lock()
        self._task_done_event: Optional[asyncio.Event] = None  # set when action_completed fires
        self._orchestrator: Optional[object] = None             # injected by initialise()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def current_mode(self) -> InputMode:
        return self._current_mode

    def initialise(self, orchestrator: object) -> None:
        """
        Called by lifecycle_manager after orchestrator.start().
        Wires the EventBus listener and applies the boot mode.

        Must be called before any set_mode() call.
        """
        from core.event_bus import bus

        self._orchestrator = orchestrator

        # Listen for task-completion events so pending switches know when to proceed.
        bus.subscribe("action_completed", self._on_action_completed)

        logger.info("ModeManager: initialised. Boot mode = %s.", self._current_mode.name)

        # Apply the boot mode without waiting — there are no tasks yet at boot.
        # We schedule it as a fire-and-forget so the asyncio loop handles it.
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

        Called by api/routes/system.py (POST /api/system/input-mode).

        Steps:
          1. Validate the transition is allowed.
          2. If a task is currently running, wait for action_completed
             (up to MODE_SWITCH_DRAIN_TIMEOUT seconds).
          3. Tear down the current subsystem.
          4. Start the new subsystem.
          5. Persist to .env.
          6. Publish input_mode_changed.

        Returns a dict that the API layer can return directly as JSON.
        Raises ModeTransitionError on invalid transition.
        """
        if new_mode == self._current_mode:
            return {"mode": new_mode.value, "changed": False, "reason": "already_active"}

        if new_mode not in ALLOWED_TRANSITIONS.get(self._current_mode, set()):
            raise ModeTransitionError(self._current_mode, new_mode)

        async with self._lock:
            previous_mode = self._current_mode

            # ── Wait for any active task to finish ────────────────────────────
            await self._drain_active_tasks()

            # ── Switch ────────────────────────────────────────────────────────
            logger.info(
                "ModeManager: switching %s → %s …",
                previous_mode.name, new_mode.name,
            )

            await self._teardown(previous_mode)
            await self._startup(new_mode)

            self._current_mode = new_mode

            # ── Persist ───────────────────────────────────────────────────────
            self._persist_to_env(new_mode)

            # ── Notify ────────────────────────────────────────────────────────
            from core.event_bus import bus
            bus.publish(
                "input_mode_changed",
                {
                    "new_mode":      new_mode.value,
                    "previous_mode": previous_mode.value,
                },
                source="mode_manager",
            )

            logger.info("ModeManager: now in %s mode.", new_mode.name)
            return {
                "mode":          new_mode.value,
                "previous_mode": previous_mode.value,
                "changed":       True,
            }

    # ── Task drain ────────────────────────────────────────────────────────────

    async def _drain_active_tasks(self) -> None:
        """
        Wait until the orchestrator has no active tasks, or until
        _TASK_DRAIN_TIMEOUT seconds elapse, whichever comes first.
        """
        orch = self._orchestrator
        if orch is None:
            return

        active: dict = getattr(orch, "active_tasks", {})
        if not active:
            return  # nothing running — switch immediately

        logger.info(
            "ModeManager: %d task(s) in flight — waiting up to %.0fs before switching …",
            len(active), _TASK_DRAIN_TIMEOUT,
        )

        self._task_done_event = asyncio.Event()

        try:
            await asyncio.wait_for(
                self._wait_for_tasks_empty(),
                timeout=_TASK_DRAIN_TIMEOUT,
            )
            logger.info("ModeManager: all tasks finished — proceeding with switch.")
        except asyncio.TimeoutError:
            logger.warning(
                "ModeManager: drain timeout (%.0fs) reached. Switching anyway.", _TASK_DRAIN_TIMEOUT
            )
        finally:
            self._task_done_event = None

    async def _wait_for_tasks_empty(self) -> None:
        """Poll active_tasks and yield on action_completed events."""
        orch = self._orchestrator
        while True:
            if not getattr(orch, "active_tasks", {}):
                return
            if self._task_done_event:
                self._task_done_event.clear()
                try:
                    await asyncio.wait_for(self._task_done_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass  # re-check active_tasks on next iteration

    async def _on_action_completed(self, event: object) -> None:
        """EventBus callback — wakes the drain waiter when a task finishes."""
        if self._task_done_event is not None:
            self._task_done_event.set()

    # ── Teardown ──────────────────────────────────────────────────────────────

    async def _teardown(self, mode: InputMode) -> None:
        """Stop whichever subsystem is currently active."""
        if mode == InputMode.VOICE:
            await self._teardown_voice()
        elif mode == InputMode.PANEL:
            await self._teardown_panel()
        # NONE → nothing to tear down

    async def _teardown_voice(self) -> None:
        """
        Stop the voice subsystem in the correct order:
          1. Signal the wake-loop gate so detect() returns on the next tick.
          2. Stop the AudioManager (releases the mic hardware lock).
        """
        orch = self._orchestrator
        if orch is None:
            return

        logger.info("ModeManager: tearing down VOICE subsystem …")

        # 1. Gate the wake-word loop
        orch._voice_active = False  # type: ignore[attr-defined]

        # 2. Stop the mic stream
        audio_manager = getattr(orch, "audio_manager", None)
        if audio_manager is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, audio_manager.stop)
            logger.info("ModeManager: AudioManager stopped.")

        logger.info("ModeManager: VOICE teardown complete.")

    async def _teardown_panel(self) -> None:
        """
        Stop the panel subsystem via orchestrator._stop_panel().

        FIX: Previously called panel_ctrl.stop() directly via run_in_executor,
        which then called self._window.hide() from the executor thread — a
        cross-thread Qt widget call that caused the segfault.

        orchestrator._stop_panel() is the correct unified stop path:
          1. Calls panel_controller.stop() which posts QApplication.quit()
             onto the Qt thread (safe) and stops the asyncio loop.
          2. Joins the Qt thread with a timeout.
        All of this is thread-safe.
        """
        orch = self._orchestrator
        if orch is None:
            return

        # Check if the panel is actually running before trying to stop it.
        panel_thread = getattr(orch, "_panel_thread", None)
        if panel_thread is None or not panel_thread.is_alive():
            logger.info("ModeManager: no panel thread running — skipping teardown.")
            return

        logger.info("ModeManager: tearing down PANEL subsystem …")
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, orch._stop_panel)
            logger.info("ModeManager: panel stopped.")
        except Exception as exc:
            logger.warning("ModeManager: panel stop error — %s", exc)

        logger.info("ModeManager: PANEL teardown complete.")

    # ── Startup ───────────────────────────────────────────────────────────────

    async def _startup(self, mode: InputMode) -> None:
        """Start whichever subsystem the new mode requires."""
        if mode == InputMode.VOICE:
            await self._startup_voice()
        elif mode == InputMode.PANEL:
            await self._startup_panel()
        # NONE → nothing to start

    async def _startup_voice(self) -> None:
        """
        Start the voice subsystem:
          1. Re-open the AudioManager (acquires the mic).
          2. Re-enable the wake-word loop gate.
        """
        orch = self._orchestrator
        if orch is None:
            return

        logger.info("ModeManager: starting VOICE subsystem …")

        audio_manager = getattr(orch, "audio_manager", None)
        if audio_manager is not None:
            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(None, audio_manager.start)
            if not success:
                logger.error("ModeManager: AudioManager failed to start — VOICE may not work.")
            else:
                logger.info("ModeManager: AudioManager started.")

        # Re-enable the wake-word loop gate
        orch._voice_active = True  # type: ignore[attr-defined]

        logger.info("ModeManager: VOICE startup complete.")

    async def _startup_panel(self) -> None:
        """
        Start the panel subsystem via orchestrator._start_panel().

        FIX: orchestrator._start_panel() now contains a re-entry guard that
        returns immediately if the panel thread is already alive. This means:
          • At boot: _apply_mode(PANEL) → _startup_panel() → _start_panel()
            creates the panel thread. orchestrator.start() no longer calls
            _start_panel(), so there is only ever one call at boot.
          • On a panel → voice → panel round-trip: _teardown_panel() joined
            and cleared the thread, so _start_panel() creates a fresh one.
          • On a spurious double call: the guard in _start_panel() makes the
            second call a silent no-op — no second window is created.
        """
        orch = self._orchestrator
        if orch is None:
            return

        logger.info("ModeManager: starting PANEL subsystem …")

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, orch._start_panel)
            logger.info("ModeManager: PanelController started.")
        except Exception as exc:
            logger.error("ModeManager: panel startup error — %s", exc)

        logger.info("ModeManager: PANEL startup complete.")

    # ── Apply mode at boot (no drain, no teardown) ────────────────────────────

    async def _apply_mode(self, mode: InputMode) -> None:
        """
        Called once at boot to put the system into the configured starting mode.
        Skips drain and teardown because nothing is running yet.
        """
        logger.info("ModeManager: applying boot mode %s …", mode.name)
        await self._startup(mode)
        logger.info("ModeManager: boot mode %s applied.", mode.name)

    # ── .env persistence ──────────────────────────────────────────────────────

    def _persist_to_env(self, mode: InputMode) -> None:
        """
        Write CURRENT_MODE=<value> into the project's .env file.

        • If the key already exists it is updated in-place (line replaced).
        • If the key is absent it is appended.
        • If the file does not exist it is created.
        """
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