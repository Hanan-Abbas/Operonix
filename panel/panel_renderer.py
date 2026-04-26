"""
panel/panel_renderer.py

Renders the panel UI into the tab areas plus a persistent mode switcher:
  • Mode switcher strip (always visible, above the tabs)
  • Command tab  — input box + live suggestion list
  • History tab  — scrollable, re-runnable history
  • Snippets tab — saved named commands
  • Settings tab — theme chooser, hotkey, opacity, font, etc.

All colors and sizes are read from ThemeTokens.
No hex values, pixel constants, or font names live here.

FIX CHANGELOG (Step 2):
  • Persistent mode switcher strip added above the QTabWidget.
    Three buttons: 🎤 Voice / ⌨ Panel / ○ None
    The active mode button is highlighted with the accent colour.
    Clicking a button emits mode_change_requested(str) which
    panel_controller publishes to the EventBus for mode_manager.
  • mode_change_requested = pyqtSignal(str) added to PanelRenderer.
  • set_active_mode(mode_str) slot added — called via _QtBridge on every
    input_mode_changed event so the correct button stays highlighted
    regardless of whether the switch came from the panel or the dashboard.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from panel.panel_theme import ThemeTokens
from panel.suggestion_engine import ExecutionStrategy, SuggestionResult

log = logging.getLogger(__name__)

try:
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal
    from PyQt6.QtGui import QColor, QFont, QPalette
    from PyQt6.QtWidgets import (
        QAbstractItemView,
        QComboBox,
        QDoubleSpinBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QPushButton,
        QScrollArea,
        QSlider,
        QSpinBox,
        QTabWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    log.warning("panel_renderer: PyQt6 not available — renderer disabled.")

    class QWidget:  # type: ignore[no-redef]
        pass


if _HAS_QT:

    class _MethodBadge(QLabel):
        """Small coloured badge showing plugin/api/command/ui."""

        _METHOD_KEYS = {
            "plugin":  "tag_plugin",
            "api":     "tag_api",
            "command": "tag_command",
            "ui":      "tag_ui",
        }

        def __init__(self, method: str, tokens: ThemeTokens) -> None:
            super().__init__(method.upper())
            colour_attr = self._METHOD_KEYS.get(method, "tag_ui")
            colour = getattr(tokens, colour_attr, tokens.accent)
            self.setStyleSheet(
                f"""
                QLabel {{
                    background: {colour};
                    color: {tokens.accent_text};
                    font-size: {tokens.font_size_sm}pt;
                    font-family: {tokens.font_family};
                    font-weight: bold;
                    padding: 2px {tokens.spacing_unit // 2}px;
                    border-radius: {tokens.radius_sm}px;
                }}
                """
            )

    class _StrategyRow(QFrame):
        """One row in the suggestion list."""

        selected = pyqtSignal(str)

        def __init__(
            self,
            strategy: ExecutionStrategy,
            tokens: ThemeTokens,
            is_default: bool,
        ) -> None:
            super().__init__()
            self._strategy = strategy
            self._build(tokens, is_default)

        def _build(self, tokens: ThemeTokens, is_default: bool) -> None:
            sp = tokens.spacing_unit
            border = tokens.accent if is_default else tokens.border_color
            self.setStyleSheet(
                f"""
                QFrame {{
                    background: {tokens.bg_secondary};
                    border: 1px solid {border};
                    border-radius: {tokens.radius_md}px;
                    margin: {sp // 4}px 0;
                }}
                QFrame:hover {{
                    background: {tokens.bg_tertiary};
                    border-color: {tokens.accent};
                }}
                """
            )
            layout = QHBoxLayout(self)
            layout.setContentsMargins(sp, sp // 2, sp, sp // 2)
            layout.setSpacing(sp)

            badge = _MethodBadge(self._strategy.method, tokens)
            layout.addWidget(badge)

            text_col = QVBoxLayout()
            text_col.setSpacing(2)
            label = QLabel(self._strategy.label)
            label.setStyleSheet(
                f"color: {tokens.text_primary}; font-size: {tokens.font_size_base}pt;"
                f" font-family: {tokens.font_family};"
            )
            desc = QLabel(self._strategy.description)
            desc.setStyleSheet(
                f"color: {tokens.text_secondary}; font-size: {tokens.font_size_sm}pt;"
                f" font-family: {tokens.font_family};"
            )
            text_col.addWidget(label)
            text_col.addWidget(desc)
            layout.addLayout(text_col)
            layout.addStretch()

            conf_pct = int(self._strategy.confidence * 100)
            conf_colour = (
                tokens.success if conf_pct >= 70
                else tokens.warning if conf_pct >= 40
                else tokens.error
            )
            conf_label = QLabel(f"{conf_pct}%")
            conf_label.setStyleSheet(
                f"color: {conf_colour}; font-size: {tokens.font_size_sm}pt;"
                f" font-family: {tokens.font_family}; font-weight: bold;"
            )
            layout.addWidget(conf_label)

        def mousePressEvent(self, event: Any) -> None:  # type: ignore[override]
            self.selected.emit(self._strategy.method)

    # -----------------------------------------------------------------------
    # Main renderer widget
    # -----------------------------------------------------------------------

    class PanelRenderer(QWidget):
        """The full panel body."""

        query_submitted      = pyqtSignal(str, str)    # (query_text, chosen_method)
        setting_changed      = pyqtSignal(str, object) # (key, value)
        rerun_requested      = pyqtSignal(str)         # query_text from history
        mode_change_requested = pyqtSignal(str)        # mode value string e.g. "voice"

        def __init__(
            self,
            tokens: ThemeTokens,
            all_themes: dict[str, str],
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self._tokens = tokens
            self._all_themes = all_themes
            self._current_result: SuggestionResult | None = None
            self._chosen_method: str | None = None
            self._debounce_timer: QTimer | None = None
            self._suggest_callback: Callable[[str], None] | None = None
            # Track which mode button is active so we can restyle on update.
            self._active_mode: str = "panel"
            # References to the mode buttons for highlight updates.
            self._mode_buttons: dict[str, QPushButton] = {}
            self._build()

        # ------------------------------------------------------------------
        # Construction
        # ------------------------------------------------------------------

        def _build(self) -> None:
            t = self._tokens
            sp = t.spacing_unit
            self.setStyleSheet(
                f"""
                QWidget {{
                    background: {t.bg_primary};
                    color: {t.text_primary};
                    font-family: {t.font_family};
                    font-size: {t.font_size_base}pt;
                }}
                QTabWidget::pane {{
                    border: 1px solid {t.border_color};
                    border-radius: {t.radius_md}px;
                    background: {t.bg_primary};
                }}
                QTabBar::tab {{
                    background: {t.bg_secondary};
                    color: {t.text_muted};
                    padding: {sp // 2}px {sp}px;
                    border-top-left-radius: {t.radius_sm}px;
                    border-top-right-radius: {t.radius_sm}px;
                    font-size: {t.font_size_sm}pt;
                    font-family: {t.font_family};
                    margin-right: 2px;
                }}
                QTabBar::tab:selected {{
                    background: {t.bg_primary};
                    color: {t.accent};
                    font-weight: bold;
                }}
                QScrollBar:vertical {{
                    background: {t.bg_secondary};
                    width: {sp + 2}px;
                    border-radius: {t.radius_sm}px;
                }}
                QScrollBar::handle:vertical {{
                    background: {t.scrollbar_color};
                    border-radius: {t.radius_sm}px;
                }}
                """
            )
            root = QVBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)

            # ── Persistent mode switcher strip ────────────────────────────
            root.addWidget(self._build_mode_switcher())

            # ── Tab area ──────────────────────────────────────────────────
            self._tabs = QTabWidget()
            self._tabs.addTab(self._build_command_tab(),  "Command")
            self._tabs.addTab(self._build_history_tab(),  "History")
            self._tabs.addTab(self._build_snippets_tab(), "Snippets")
            self._tabs.addTab(self._build_settings_tab(), "Settings")
            root.addWidget(self._tabs)

        def _build_mode_switcher(self) -> QWidget:
            """
            Thin persistent strip above the tabs with three mode buttons.
            Always visible regardless of the active tab.

            Buttons:  🎤 Voice  |  ⌨ Panel  |  ○ None
            The active mode button is highlighted with the accent colour.
            Clicking a non-active button emits mode_change_requested(mode).
            """
            t = self._tokens
            sp = t.spacing_unit

            strip = QWidget()
            strip.setFixedHeight(sp * 5)
            strip.setStyleSheet(
                f"""
                QWidget {{
                    background: {t.bg_secondary};
                    border-bottom: 1px solid {t.border_color};
                }}
                """
            )
            layout = QHBoxLayout(strip)
            layout.setContentsMargins(sp, sp // 2, sp, sp // 2)
            layout.setSpacing(sp // 2)

            mode_label = QLabel("Input:")
            mode_label.setStyleSheet(
                f"color: {t.text_muted}; font-size: {t.font_size_sm}pt;"
                f" background: transparent; border: none;"
            )
            layout.addWidget(mode_label)

            self._mode_buttons = {}
            modes = [
                ("voice", "🎤 Voice"),
                ("panel", "⌨ Panel"),
                ("none",  "○ None"),
            ]
            for mode_val, mode_label_text in modes:
                btn = QPushButton(mode_label_text)
                btn.setCheckable(False)
                btn.setFixedHeight(sp * 3)
                self._mode_buttons[mode_val] = btn
                self._style_mode_button(btn, mode_val, t)
                # Capture mode_val in closure correctly.
                btn.clicked.connect(
                    (lambda mv: lambda: self._on_mode_btn_clicked(mv))(mode_val)
                )
                layout.addWidget(btn)

            layout.addStretch()

            # Apply initial highlight.
            self._highlight_mode_button(self._active_mode, t)

            return strip

        def _style_mode_button(
            self,
            btn: QPushButton,
            mode_val: str,
            t: ThemeTokens,
            active: bool = False,
        ) -> None:
            """Apply active or inactive style to a mode button."""
            sp = t.spacing_unit
            if active:
                btn.setStyleSheet(
                    f"""
                    QPushButton {{
                        background: {t.accent};
                        color: {t.accent_text};
                        border: none;
                        border-radius: {t.radius_sm}px;
                        padding: 2px {sp}px;
                        font-size: {t.font_size_sm}pt;
                        font-family: {t.font_family};
                        font-weight: bold;
                    }}
                    QPushButton:hover {{
                        background: {t.accent};
                        opacity: 0.85;
                    }}
                    """
                )
            else:
                btn.setStyleSheet(
                    f"""
                    QPushButton {{
                        background: transparent;
                        color: {t.text_muted};
                        border: 1px solid {t.border_color};
                        border-radius: {t.radius_sm}px;
                        padding: 2px {sp}px;
                        font-size: {t.font_size_sm}pt;
                        font-family: {t.font_family};
                    }}
                    QPushButton:hover {{
                        background: {t.bg_tertiary};
                        color: {t.text_primary};
                        border-color: {t.accent};
                    }}
                    """
                )

        def _highlight_mode_button(self, active_mode: str, t: ThemeTokens) -> None:
            """Style all mode buttons, highlighting only the active one."""
            for mode_val, btn in self._mode_buttons.items():
                self._style_mode_button(btn, mode_val, t, active=(mode_val == active_mode))

        def _on_mode_btn_clicked(self, mode_val: str) -> None:
            """User clicked a mode button — emit signal for panel_controller."""
            if mode_val == self._active_mode:
                return  # already active — no-op
            self.mode_change_requested.emit(mode_val)

        # ------------------------------------------------------------------
        # Command tab
        # ------------------------------------------------------------------

        def _build_command_tab(self) -> QWidget:
            t = self._tokens
            sp = t.spacing_unit
            w = QWidget()
            layout = QVBoxLayout(w)
            layout.setContentsMargins(sp, sp, sp, sp)
            layout.setSpacing(sp // 2)

            self._app_badge = QLabel("App: —")
            self._app_badge.setStyleSheet(
                f"color: {t.text_muted}; font-size: {t.font_size_sm}pt;"
            )
            layout.addWidget(self._app_badge)

            self._input = QLineEdit()
            self._input.setPlaceholderText("Type a command…")
            self._input.setStyleSheet(
                f"""
                QLineEdit {{
                    background: {t.bg_secondary};
                    color: {t.text_primary};
                    border: 1px solid {t.border_color};
                    border-radius: {t.radius_md}px;
                    padding: {sp}px;
                    font-size: {t.font_size_base}pt;
                    font-family: {t.font_family};
                    selection-background-color: {t.selection_bg};
                }}
                QLineEdit:focus {{
                    border-color: {t.accent};
                }}
                """
            )
            self._input.textChanged.connect(self._on_text_changed)
            self._input.returnPressed.connect(self._on_enter)
            layout.addWidget(self._input)

            self._strategy_container = QVBoxLayout()
            self._strategy_container.setSpacing(2)
            strategy_wrap = QWidget()
            strategy_wrap.setLayout(self._strategy_container)
            scroll = QScrollArea()
            scroll.setWidget(strategy_wrap)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            layout.addWidget(scroll)

            self._status_label = QLabel("")
            self._status_label.setStyleSheet(
                f"color: {t.text_muted}; font-size: {t.font_size_sm}pt;"
            )
            layout.addWidget(self._status_label)

            return w

        # ------------------------------------------------------------------
        # History tab
        # ------------------------------------------------------------------

        def _build_history_tab(self) -> QWidget:
            t = self._tokens
            sp = t.spacing_unit
            w = QWidget()
            layout = QVBoxLayout(w)
            layout.setContentsMargins(sp, sp, sp, sp)

            self._history_list = QListWidget()
            self._history_list.setStyleSheet(
                f"""
                QListWidget {{
                    background: {t.bg_secondary};
                    border: 1px solid {t.border_color};
                    border-radius: {t.radius_md}px;
                    color: {t.text_primary};
                    font-family: {t.font_family};
                    font-size: {t.font_size_sm}pt;
                }}
                QListWidget::item:selected {{
                    background: {t.selection_bg};
                    color: {t.accent};
                }}
                QListWidget::item:hover {{
                    background: {t.bg_tertiary};
                }}
                """
            )
            self._history_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self._history_list.itemDoubleClicked.connect(self._on_history_rerun)
            layout.addWidget(self._history_list)

            hint = QLabel("Double-click to re-run")
            hint.setStyleSheet(f"color: {t.text_muted}; font-size: {t.font_size_sm}pt;")
            layout.addWidget(hint)
            return w

        # ------------------------------------------------------------------
        # Snippets tab
        # ------------------------------------------------------------------

        def _build_snippets_tab(self) -> QWidget:
            t = self._tokens
            sp = t.spacing_unit
            w = QWidget()
            layout = QVBoxLayout(w)
            layout.setContentsMargins(sp, sp, sp, sp)
            layout.setSpacing(sp // 2)

            self._snippet_list = QListWidget()
            self._snippet_list.setStyleSheet(
                f"""
                QListWidget {{
                    background: {t.bg_secondary};
                    border: 1px solid {t.border_color};
                    border-radius: {t.radius_md}px;
                    color: {t.text_primary};
                    font-family: {t.font_family};
                    font-size: {t.font_size_sm}pt;
                }}
                QListWidget::item:selected {{
                    background: {t.selection_bg};
                    color: {t.accent};
                }}
                """
            )
            self._snippet_list.itemDoubleClicked.connect(self._on_snippet_expand)
            layout.addWidget(self._snippet_list)
            return w

        # ------------------------------------------------------------------
        # Settings tab
        # ------------------------------------------------------------------

        def _build_settings_tab(self) -> QWidget:
            t = self._tokens
            sp = t.spacing_unit

            def _row_label(text: str) -> QLabel:
                lbl = QLabel(text)
                lbl.setStyleSheet(
                    f"color: {t.text_secondary}; font-size: {t.font_size_sm}pt;"
                    f" font-family: {t.font_family};"
                )
                return lbl

            w = QWidget()
            layout = QVBoxLayout(w)
            layout.setContentsMargins(sp, sp, sp, sp)
            layout.setSpacing(sp)

            layout.addWidget(_row_label("Theme"))
            self._theme_combo = QComboBox()
            self._theme_combo.setStyleSheet(
                f"""
                QComboBox {{
                    background: {t.bg_secondary};
                    color: {t.text_primary};
                    border: 1px solid {t.border_color};
                    border-radius: {t.radius_sm}px;
                    padding: {sp // 2}px {sp}px;
                    font-family: {t.font_family};
                    font-size: {t.font_size_sm}pt;
                }}
                QComboBox QAbstractItemView {{
                    background: {t.bg_secondary};
                    color: {t.text_primary};
                    selection-background-color: {t.selection_bg};
                }}
                """
            )
            for key, label in self._all_themes.items():
                self._theme_combo.addItem(label, userData=key)
            self._theme_combo.currentIndexChanged.connect(
                lambda _: self.setting_changed.emit(
                    "theme", self._theme_combo.currentData()
                )
            )
            layout.addWidget(self._theme_combo)

            layout.addWidget(_row_label("Opacity"))
            opacity_row = QHBoxLayout()
            self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
            self._opacity_slider.setRange(20, 100)
            self._opacity_slider.setValue(96)
            self._opacity_label = QLabel("96%")
            self._opacity_label.setStyleSheet(f"color: {t.text_muted};")
            self._opacity_slider.valueChanged.connect(
                lambda v: (
                    self._opacity_label.setText(f"{v}%"),
                    self.setting_changed.emit("opacity", v / 100.0),
                )
            )
            opacity_row.addWidget(self._opacity_slider)
            opacity_row.addWidget(self._opacity_label)
            layout.addLayout(opacity_row)

            layout.addWidget(_row_label("Font size (pt)"))
            self._font_spin = QSpinBox()
            self._font_spin.setRange(8, 24)
            self._font_spin.setValue(t.font_size_base)
            self._font_spin.setStyleSheet(
                f"background: {t.bg_secondary}; color: {t.text_primary};"
                f" border: 1px solid {t.border_color}; border-radius: {t.radius_sm}px;"
                f" padding: 2px; font-family: {t.font_family};"
            )
            self._font_spin.valueChanged.connect(
                lambda v: self.setting_changed.emit("font_size", v)
            )
            layout.addWidget(self._font_spin)

            layout.addWidget(_row_label("Global hotkey"))
            self._hotkey_input = QLineEdit()
            self._hotkey_input.setPlaceholderText("<ctrl>+<space>")
            self._hotkey_input.setStyleSheet(
                f"background: {t.bg_secondary}; color: {t.text_primary};"
                f" border: 1px solid {t.border_color}; border-radius: {t.radius_sm}px;"
                f" padding: {sp // 2}px; font-family: {t.font_family};"
            )
            self._hotkey_input.editingFinished.connect(
                lambda: self.setting_changed.emit("hotkey", self._hotkey_input.text())
            )
            layout.addWidget(self._hotkey_input)

            layout.addStretch()
            return w

        # ------------------------------------------------------------------
        # Public update methods (called via _QtBridge from panel_controller)
        # ------------------------------------------------------------------

        def set_tokens(self, tokens: ThemeTokens) -> None:
            """Swap in a new theme; rebuilds the stylesheet."""
            self._tokens = tokens
            old_layout = self.layout()
            if old_layout is not None:
                while old_layout.count():
                    item = old_layout.takeAt(0)
                    w = item.widget()
                    if w:
                        w.setParent(None)  # type: ignore[arg-type]
                        w.deleteLater()
                import PyQt6.QtWidgets as _qw
                _dummy = _qw.QWidget()
                _dummy.setLayout(old_layout)
            self._build()
            log.info("panel_renderer: theme applied")

        def set_active_mode(self, mode_str: str) -> None:
            """
            Highlight the button matching mode_str and un-highlight the others.
            Called via _QtBridge.sig_set_active_mode (safe from any thread via signal).
            Also called directly in start() to set the initial state.
            """
            self._active_mode = mode_str
            if self._mode_buttons:
                self._highlight_mode_button(mode_str, self._tokens)

        def update_suggestions(self, result: SuggestionResult) -> None:
            self._current_result = result
            self._chosen_method = result.top.method if result.top else None

            while self._strategy_container.count():
                item = self._strategy_container.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            for i, strategy in enumerate(result.strategies):
                row = _StrategyRow(strategy, self._tokens, is_default=(i == 0))
                row.selected.connect(self._on_strategy_selected)
                self._strategy_container.addWidget(row)

            app_text = f"App: {result.app_context or '—'}"
            intent_text = f"  ·  Intent: {result.intent}" if result.intent else ""
            self._app_badge.setText(app_text + intent_text)

        def push_history_item(self, text: str, method: str, success: bool) -> None:
            icon = "✓" if success else "✗"
            colour = self._tokens.success if success else self._tokens.error
            item = QListWidgetItem(f"{icon}  [{method}]  {text}")
            item.setForeground(QColor(colour))
            self._history_list.insertItem(0, item)

        def load_snippets(self, snippets: list[Any]) -> None:
            self._snippet_list.clear()
            for s in snippets:
                item = QListWidgetItem(f"{s.name}  →  {s.query_text[:60]}")
                item.setData(Qt.ItemDataRole.UserRole, s.query_text)
                self._snippet_list.addItem(item)

        def set_status(self, message: str, level: str = "info") -> None:
            colour_map = {
                "info":    self._tokens.info,
                "success": self._tokens.success,
                "warning": self._tokens.warning,
                "error":   self._tokens.error,
            }
            colour = colour_map.get(level, self._tokens.text_muted)
            self._status_label.setStyleSheet(
                f"color: {colour}; font-size: {self._tokens.font_size_sm}pt;"
            )
            self._status_label.setText(message)

        def set_suggest_callback(self, cb: Callable[[str], None]) -> None:
            self._suggest_callback = cb

        def set_app_context(self, app_name: str) -> None:
            current = self._app_badge.text()
            intent_part = ""
            if "  ·  " in current:
                intent_part = current[current.index("  ·  "):]
            self._app_badge.setText(f"App: {app_name or '—'}{intent_part}")

        def set_resolved_intent(self, intent: str) -> None:
            current = self._app_badge.text()
            app_part = current.split("  ·  ")[0] if "  ·  " in current else current
            self._app_badge.setText(f"{app_part}  ·  Intent: {intent}")

        def set_theme_selection(self, theme_key: str) -> None:
            for i in range(self._theme_combo.count()):
                if self._theme_combo.itemData(i) == theme_key:
                    self._theme_combo.setCurrentIndex(i)
                    return

        def set_opacity_value(self, opacity: float) -> None:
            self._opacity_slider.setValue(int(opacity * 100))

        def set_font_size_value(self, size: int) -> None:
            self._font_spin.setValue(size)

        def set_hotkey_value(self, hotkey: str) -> None:
            self._hotkey_input.setText(hotkey)

        # ------------------------------------------------------------------
        # Slot handlers
        # ------------------------------------------------------------------

        def _on_text_changed(self, text: str) -> None:
            if not self._suggest_callback:
                return
            if self._debounce_timer:
                self._debounce_timer.stop()
            self._debounce_timer = QTimer()
            self._debounce_timer.setSingleShot(True)
            self._debounce_timer.timeout.connect(lambda: self._suggest_callback(text))
            self._debounce_timer.start(self._tokens.spacing_unit * 31)

        def _on_enter(self) -> None:
            text = self._input.text().strip()
            if not text:
                return
            method = self._chosen_method or "command"
            self.query_submitted.emit(text, method)
            self._input.clear()

        def _on_strategy_selected(self, method: str) -> None:
            self._chosen_method = method
            for i in range(self._strategy_container.count()):
                widget = self._strategy_container.itemAt(i).widget()
                if isinstance(widget, _StrategyRow):
                    is_selected = (widget._strategy.method == method)
                    t = self._tokens
                    border = t.accent if is_selected else t.border_color
                    widget.setStyleSheet(
                        widget.styleSheet().replace(
                            f"border: 1px solid {t.border_color}",
                            f"border: 1px solid {border}",
                        )
                    )

        def _on_history_rerun(self, item: Any) -> None:
            query = item.text().split("]  ", 1)[-1].strip()
            self.rerun_requested.emit(query)
            self._input.setText(query)
            self._tabs.setCurrentIndex(0)

        def _on_snippet_expand(self, item: Any) -> None:
            query = item.data(Qt.ItemDataRole.UserRole)
            if query:
                self._input.setText(query)
                self._tabs.setCurrentIndex(0)
                self._input.setFocus()

else:
    class PanelRenderer:  # type: ignore[no-redef]
        def __init__(self, *a: Any, **kw: Any) -> None:
            log.warning("PanelRenderer: PyQt6 not available.")
        def set_active_mode(self, mode_str: str) -> None: pass
        def set_app_context(self, app: str) -> None: pass
        def set_status(self, msg: str, level: str = "info") -> None: pass