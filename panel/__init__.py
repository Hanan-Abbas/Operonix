"""
panel/ — Operonix Command Panel
A standalone, event-driven GUI module that acts as a parallel text input
source alongside the voice pipeline. Plugs into the EventBus and never
couples directly to any other subsystem.
"""
from __future__ import annotations

from panel.panel_controller import PanelController
from panel.panel_state import PanelState
from panel.panel_config import PanelConfig

__all__ = ["PanelController", "PanelState", "PanelConfig"]