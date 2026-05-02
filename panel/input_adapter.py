"""
panel/input_adapter.py — Operonix AI OS Agent
══════════════════════════════════════════════
Bridges the panel UI (PanelRenderer.query_submitted signal) to the
EventBus by publishing "text_query_received" — the same event the
orchestrator already listens for via _handle_panel_input().

This makes the panel the exact text-side mirror of voice/pipeline.py:

  Voice path:   AudioManager → VoicePipeline.capture_command()
                  → bus.publish("transcription_complete")
                  → orchestrator normalises to "user_input_received"

  Panel path:   PanelRenderer.query_submitted signal
                  → PanelInputAdapter.on_query_submitted()
                  → bus.publish("text_query_received")
                  → orchestrator normalises to "user_input_received"

Neither path knows about the other. The orchestrator is unchanged.

Lifecycle:
  • PanelInputAdapter.start() is called by PanelController when the panel
    starts (or by mode_manager._startup_panel indirectly via _start_panel).
  • PanelInputAdapter.stop() disconnects the signal — no more events fire.

No Qt imports at module level so this file loads safely in headless mode.

"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from core.event_bus import bus

if TYPE_CHECKING:
    from panel.panel_renderer import PanelRenderer
    from panel.panel_state import PanelState

logger = logging.getLogger("PanelInputAdapter")


class PanelInputAdapter:
    """
    Connects PanelRenderer.query_submitted → EventBus("text_query_received").

    Instantiated once by PanelController and passed to it.

    Args:
        panel_state: PanelState instance. The adapter reads
                     panel_state.pre_panel_context on every submission to
                     inject the correct cwd into the query payload.
    """

    def __init__(self, panel_state: "PanelState") -> None:
        self._panel_state = panel_state
        self._renderer: Optional[PanelRenderer] = None
        self._connected: bool = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self, renderer: "PanelRenderer") -> None:
        """
        Connect to the renderer's query_submitted signal.

        Must be called after the PanelRenderer widget has been constructed
        (i.e. inside the Qt thread, after QApplication.exec() has started).

        Args:
            renderer: the live PanelRenderer instance.
        """
        if self._connected:
            logger.warning("PanelInputAdapter: already connected — ignoring duplicate start().")
            return

        self._renderer = renderer

        try:
            # PanelRenderer.query_submitted emits (query_text: str, chosen_method: str)
            renderer.query_submitted.connect(self.on_query_submitted)
            self._connected = True
            logger.info("PanelInputAdapter: connected to PanelRenderer.query_submitted.")
        except Exception as exc:
            logger.error("PanelInputAdapter: failed to connect signal — %s", exc)

    def stop(self) -> None:
        """
        Disconnect from the renderer signal.

        Safe to call even if start() was never called or already stopped.
        """
        if not self._connected or self._renderer is None:
            return

        try:
            self._renderer.query_submitted.disconnect(self.on_query_submitted)
            logger.info("PanelInputAdapter: disconnected from PanelRenderer.")
        except Exception as exc:
            logger.debug("PanelInputAdapter: disconnect error (benign) — %s", exc)
        finally:
            self._connected = False
            self._renderer = None

    # ── Signal handler ────────────────────────────────────────────────────────

    def on_query_submitted(self, query_text: str, chosen_method: str) -> None:
        """
        Called by Qt signal in the panel thread when the user submits a command.

        Publishes "text_query_received" to the EventBus.
        bus.publish() is thread-safe (uses call_soon_threadsafe internally)
        so calling it from the Qt thread is correct and safe.

        The orchestrator's _handle_panel_input() subscribes to this event
        and normalises it into "user_input_received" — identical to the
        voice path. Neither the brain nor the executor ever sees "panel"
        vs "voice" in their hot paths.

        Args:
            query_text:    The raw text typed by the user.
            chosen_method: One of "plugin", "api", "command", "ui", or "auto"
                           as selected by the user in the strategy list.
        """
        query = query_text.strip()
        if not query:
            logger.debug("PanelInputAdapter: empty query ignored.")
            return

        # Read the context that was snapshotted by HotkeyListener *before*
        # the panel window appeared. This is the user's real working context.
        pre_context: Optional[dict] = self._panel_state.pre_panel_context

        payload: dict[str, Any] = {
            "query":            query,
            "preferred_method": chosen_method if chosen_method != "auto" else None,
            "source":           "panel",
        }

        if pre_context is not None:
            try:
                from context.window_detector import window_detector as _wd
                _wd._last_external_snapshot = dict(pre_context)
                logger.debug(
                    "PanelInputAdapter: restored _last_external_snapshot "
                    "cwd=%r window=%r",
                    pre_context.get("cwd"),
                    pre_context.get("window_title"),
                )
            except Exception as exc:
                logger.warning(
                    "PanelInputAdapter: could not restore snapshot — %s", exc
                )

            payload["pre_panel_context"] = pre_context
            logger.info(
                "PanelInputAdapter: publishing query (method=%r, cwd=%r): %r",
                chosen_method,
                pre_context.get("cwd"),
                query,
            )
        else:
            logger.info(
                "PanelInputAdapter: publishing query (method=%r, no pre-context): %r",
                chosen_method,
                query,
            )

        bus.publish(
            "text_query_received",
            payload,
            source="panel_input_adapter",
        )