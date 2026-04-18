"""
panel/panel_window.py

The actual OS-level window.
  • Frameless but draggable via title-bar strip
  • Always on top (Qt.WindowType.Tool)
  • NEVER steals keyboard focus from the active application
  • Minimum 400×140 px, default geometry from PanelState
  • Opacity, position, size, pin all driven by PanelState — never hardcoded
"""
from __future__ import annotations

import logging
from typing import Any

from panel.panel_theme import ThemeTokens

log = logging.getLogger(__name__)

try:
    from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
    from PyQt6.QtGui import QColor, QPainter, QPalette
    from PyQt6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QSizeGrip,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    log.warning("panel_window: PyQt6 not available — window disabled.")

    class QWidget:  # type: ignore[no-redef]
        pass


_MIN_WIDTH = 400
_MIN_HEIGHT = 140


if _HAS_QT:

    class _TitleBar(QWidget):
        """
        Narrow title strip at the top of the panel.
        Dragging here moves the window.
        Clicking the close/pin buttons fires signals.
        """
        close_clicked = pyqtSignal()
        pin_clicked = pyqtSignal()

        def __init__(self, tokens: ThemeTokens, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._tokens = tokens
            self._drag_pos: QPoint | None = None
            self._build()

        def _build(self) -> None:
            t = self._tokens
            sp = t.spacing_unit
            self.setFixedHeight(sp * 4)
            self.setStyleSheet(
                f"""
                QWidget {{
                    background: {t.bg_secondary};
                    border-bottom: 1px solid {t.border_color};
                    border-top-left-radius: {t.radius_lg}px;
                    border-top-right-radius: {t.radius_lg}px;
                }}
                """
            )
            layout = QHBoxLayout(self)
            layout.setContentsMargins(sp, 0, sp, 0)

            # Operonix logo / label
            title = QLabel("⬡ Operonix")
            title.setStyleSheet(
                f"color: {t.accent}; font-weight: bold; font-size: {t.font_size_sm}pt;"
                f" font-family: {t.font_family}; background: transparent; border: none;"
            )
            layout.addWidget(title)
            layout.addStretch()

            # Pin button
            self._pin_btn = QPushButton("📌")
            self._pin_btn.setFixedSize(QSize(sp * 3, sp * 3))
            self._pin_btn.setToolTip("Pin panel (keep visible when idle)")
            self._pin_btn.setStyleSheet(self._btn_style(t))
            self._pin_btn.clicked.connect(self.pin_clicked)
            layout.addWidget(self._pin_btn)

            # Close button
            close_btn = QPushButton("✕")
            close_btn.setFixedSize(QSize(sp * 3, sp * 3))
            close_btn.setToolTip("Hide panel")
            close_btn.setStyleSheet(self._btn_style(t, hover_colour=t.error))
            close_btn.clicked.connect(self.close_clicked)
            layout.addWidget(close_btn)

        @staticmethod
        def _btn_style(t: ThemeTokens, hover_colour: str | None = None) -> str:
            hover = hover_colour or t.accent
            return (
                f"QPushButton {{ background: transparent; color: {t.text_muted};"
                f" border: none; border-radius: {t.radius_sm}px; font-size: {t.font_size_sm}pt; }}"
                f" QPushButton:hover {{ background: {hover}30; color: {hover}; }}"
            )

        # Drag support
        def mousePressEvent(self, event: Any) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
                event.accept()

        def mouseMoveEvent(self, event: Any) -> None:
            if self._drag_pos and event.buttons() == Qt.MouseButton.LeftButton:
                self.window().move(event.globalPosition().toPoint() - self._drag_pos)
                event.accept()

        def mouseReleaseEvent(self, event: Any) -> None:
            self._drag_pos = None

        def update_pin_icon(self, pinned: bool) -> None:
            self._pin_btn.setText("📍" if pinned else "📌")

        def apply_tokens(self, tokens: ThemeTokens) -> None:
            self._tokens = tokens
            self._build()

    # -----------------------------------------------------------------------

    class PanelWindow(QWidget):
        """
        The top-level frameless panel window.

        It does NOT own the renderer — the renderer is passed in by
        panel_controller after creation, so the window stays as thin as
        possible.

        Signals:
            hidden()  — emitted when the panel is hidden
            pinned(bool) — emitted when the pin state changes
        """
        hidden = pyqtSignal()
        pinned = pyqtSignal(bool)

        def __init__(self, tokens: ThemeTokens, state: Any) -> None:
            """
            Args:
                tokens: Resolved ThemeTokens for the initial theme.
                state:  PanelState instance (read geometry/opacity from here).
            """
            super().__init__()
            self._tokens = tokens
            self._state = state
            self._is_pinned = state.pinned
            self._body_widget: QWidget | None = None
            self._setup_window()
            self._build_chrome()

        # ------------------------------------------------------------------
        # Window setup
        # ------------------------------------------------------------------

        def _setup_window(self) -> None:
            s = self._state
            self.setWindowFlags(
                Qt.WindowType.Tool
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint,
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            self.setMinimumSize(_MIN_WIDTH, _MIN_HEIGHT)
            self.resize(s.width, s.height)
            self.move(s.x, s.y)
            self.setWindowOpacity(s.opacity)

        def _build_chrome(self) -> None:
            t = self._tokens
            self.setStyleSheet(
                f"""
                PanelWindow {{
                    background: {t.bg_primary};
                    border: 1px solid {t.border_color};
                    border-radius: {t.radius_lg}px;
                }}
                """
            )
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)

            self._title_bar = _TitleBar(t, parent=self)
            self._title_bar.close_clicked.connect(self.hide_panel)
            self._title_bar.pin_clicked.connect(self._toggle_pin)
            outer.addWidget(self._title_bar)

            # Placeholder — real renderer inserted by set_body()
            self._body_slot = QVBoxLayout()
            self._body_slot.setContentsMargins(0, 0, 0, 0)
            outer.addLayout(self._body_slot)

            # Resize grip in the bottom-right corner.
            grip_row = QHBoxLayout()
            grip_row.addStretch()
            grip = QSizeGrip(self)
            grip.setStyleSheet("background: transparent;")
            grip_row.addWidget(grip)
            outer.addLayout(grip_row)

        # ------------------------------------------------------------------
        # Public API
        # ------------------------------------------------------------------

        def set_body(self, widget: QWidget) -> None:
            """Insert the renderer widget into the body slot."""
            self._body_widget = widget
            self._body_slot.addWidget(widget)

        def show_panel(self) -> None:
            self.show()
            self.raise_()
            # Do NOT call activateWindow() — that would steal focus.
            log.debug("panel_window: shown")

        def hide_panel(self) -> None:
            if not self._is_pinned:
                self.hide()
                self.hidden.emit()
                log.debug("panel_window: hidden")

        def toggle(self) -> None:
            if self.isVisible():
                self.hide_panel()
            else:
                self.show_panel()

        def set_position(self, x: int, y: int) -> None:
            self.move(x, y)

        def set_size(self, width: int, height: int) -> None:
            self.resize(
                max(width, _MIN_WIDTH),
                max(height, _MIN_HEIGHT),
            )

        def apply_tokens(self, tokens: ThemeTokens) -> None:
            """Hot-swap the theme without recreating the window."""
            self._tokens = tokens
            t = tokens
            self.setStyleSheet(
                f"""
                PanelWindow {{
                    background: {t.bg_primary};
                    border: 1px solid {t.border_color};
                    border-radius: {t.radius_lg}px;
                }}
                """
            )
            self._title_bar.apply_tokens(tokens)
            self.update()

        def apply_opacity(self, opacity: float) -> None:
            self.setWindowOpacity(max(0.2, min(1.0, opacity)))

        # ------------------------------------------------------------------
        # Internal
        # ------------------------------------------------------------------

        def _toggle_pin(self) -> None:
            self._is_pinned = not self._is_pinned
            self._title_bar.update_pin_icon(self._is_pinned)
            self.pinned.emit(self._is_pinned)
            log.info("panel_window: pinned=%s", self._is_pinned)

        def closeEvent(self, event: Any) -> None:  # type: ignore[override]
            # Intercept close — just hide rather than destroy.
            event.ignore()
            self.hide_panel()

        def moveEvent(self, event: Any) -> None:
            # Persist geometry as the user moves the window.
            pos = self.pos()
            self._state.update(x=pos.x(), y=pos.y())

        def resizeEvent(self, event: Any) -> None:
            size = self.size()
            self._state.update(width=size.width(), height=size.height())

else:
    class PanelWindow:  # type: ignore[no-redef]
        def __init__(self, *a: Any, **kw: Any) -> None:
            log.warning("PanelWindow: PyQt6 not available.")

        def show_panel(self) -> None: pass
        def hide_panel(self) -> None: pass
        def toggle(self) -> None: pass