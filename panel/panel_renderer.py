"""
panel/panel_renderer.py

Renders the panel UI into the three tab areas:
  • Command tab  — input box + live suggestion list
  • History tab  — scrollable, re-runnable history
  • Snippets tab — saved named commands
  • Settings tab — theme chooser, hotkey, opacity, font, etc.

All colors and sizes are read from ThemeTokens.
No hex values, pixel constants, or font names live here.
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

    # Stub base so the module still imports cleanly.
    class QWidget:  # type: ignore[no-redef]
        pass


if _HAS_QT:

    class _MethodBadge(QLabel):
        """Small coloured badge showing plugin/api/command/ui."""

        _METHOD_KEYS = {
            "plugin": "tag_plugin",
            "api": "tag_api",
            "command": "tag_command",
            "ui": "tag_ui",
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

        selected = pyqtSignal(str)  # emits method string when clicked

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
            conf_label = QLabel(f"{conf_pct}%")
            conf_colour = (
                tokens.success if conf_pct >= 70
                else tokens.warning if conf_pct >= 40
                else tokens.error
            )
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
        """
        The full panel body. Owned by panel_window.py which calls
        set_tokens() whenever the theme changes.
        """

        # Signals consumed by panel_controller.py
        query_submitted = pyqtSignal(str, str)     # (query_text, chosen_method)
        setting_changed = pyqtSignal(str, object)  # (key, value)
        rerun_requested = pyqtSignal(str)          # query_text from history

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

            self._tabs = QTabWidget()
            self._tabs.addTab(self._build_command_tab(), "Command")
            self._tabs.addTab(self._build_history_tab(), "History")
            self._tabs.addTab(self._build_snippets_tab(), "Snippets")
            self._tabs.addTab(self._build_settings_tab(), "Settings")
            root.addWidget(self._tabs)

        def _build_command_tab(self) -> QWidget:
            t = self._tokens
            sp = t.spacing_unit
            w = QWidget()
            layout = QVBoxLayout(w)
            layout.setContentsMargins(sp, sp, sp, sp)
            layout.setSpacing(sp // 2)

            # App context badge
            self._app_badge = QLabel("App: —")
            self._app_badge.setStyleSheet(
                f"color: {t.text_muted}; font-size: {t.font_size_sm}pt;"
            )
            layout.addWidget(self._app_badge)

            # Input box
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

            # Strategy list
            self._strategy_container = QVBoxLayout()
            self._strategy_container.setSpacing(2)
            strategy_wrap = QWidget()
            strategy_wrap.setLayout(self._strategy_container)
            scroll = QScrollArea()
            scroll.setWidget(strategy_wrap)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            layout.addWidget(scroll)

            # Status bar
            self._status_label = QLabel("")
            self._status_label.setStyleSheet(
                f"color: {t.text_muted}; font-size: {t.font_size_sm}pt;"
            )
            layout.addWidget(self._status_label)

            return w

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

            # --- Theme chooser ---
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

            # --- Opacity slider ---
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

            # --- Font size ---
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

            # --- Hotkey ---
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
        # Public update methods (called by panel_controller)
        # ------------------------------------------------------------------

        def set_tokens(self, tokens: ThemeTokens) -> None:
            """Swap in a new theme; rebuilds the stylesheet."""
            self._tokens = tokens
            # Remove the old top-level layout and all its children safely.
            old_layout = self.layout()
            if old_layout is not None:
                # Drain widgets from the old layout before deleting it.
                while old_layout.count():
                    item = old_layout.takeAt(0)
                    w = item.widget()
                    if w:
                        w.setParent(None)  # type: ignore[arg-type]
                        w.deleteLater()
                # Qt requires the layout to be re-parented to a throw-away
                # widget before we can assign a new one to self.
                import PyQt6.QtWidgets as _qw
                _dummy = _qw.QWidget()
                _dummy.setLayout(old_layout)
            self._build()
            log.info("panel_renderer: theme applied")

        def update_suggestions(self, result: SuggestionResult) -> None:
            """Refresh the strategy list in the Command tab."""
            self._current_result = result
            self._chosen_method = result.top.method if result.top else None

            # Clear previous rows.
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
            """Register the async suggestion trigger (wired by panel_controller)."""
            self._suggest_callback = cb

        def set_theme_selection(self, theme_key: str) -> None:
            """Programmatically select a theme in the combo box."""
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
            self._debounce_timer.start(self._tokens.spacing_unit * 31)  # ~248 ms at sp=8

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
    # Stub for non-Qt environments.
    class PanelRenderer:  # type: ignore[no-redef]
        def __init__(self, *a: Any, **kw: Any) -> None:
            log.warning("PanelRenderer: PyQt6 not available.")