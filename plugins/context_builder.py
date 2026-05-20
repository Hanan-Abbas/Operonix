"""
plugins/context_builder.py

Builds a scoped service-context dict for a plugin based on the
`allowed_services` list declared in its PluginManifest.

Only the services the plugin explicitly declared are instantiated and
handed in — nothing else is reachable from inside the sandbox.

Usage (called exclusively by sandbox_runner.py):

    from plugins.context_builder import PluginContextBuilder
    builder = PluginContextBuilder()
    ctx = builder.build(manifest.allowed_services)
    result = await plugin.run(context, args, service_ctx=ctx)

Plugins access services through the injected dict:

    shell   = service_ctx.get("shell_tool")
    term    = service_ctx.get("terminal_resolver")
    window  = service_ctx.get("window_context")      # callable → fresh snapshot
    files   = service_ctx.get("file_tool")

A missing key means the plugin never declared that service — not a
runtime error in the provider. Plugins should always guard:

    svc = service_ctx.get("shell_tool")
    if svc is None:
        return {"status": "error", "message": "shell_tool not available"}
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("PluginContextBuilder")

# ── Canonical service registry ────────────────────────────────────────────────
# Maps every valid allowed_services token → the provider method that
# instantiates it.  Add new services here; nothing else needs to change.

VALID_SERVICES: frozenset[str] = frozenset({
    # context / UI
    "window_context",       # context.window_detector + context.focus_tracker
    "app_classifier",       # context.app_classifier
    "app_profiler",         # context.app_profiler
    "screen_reader",        # automation.screen_reader
    "selector_engine",      # automation.selector_engine
    "ui_fallback",          # automation.ui_fallback

    # shell / terminal
    "terminal_resolver",    # core.terminal_resolver
    "shell_tool",           # tools.shell_tool
    "process_bridge",       # tools.process_bridge

    # file system
    "file_tool",            # tools.file_tool
    "file_ops",             # capabilities.file_ops
    "smart_file_patcher",   # tools.smart_file_patcher

    # web / api
    "web_ops",              # capabilities.web_ops
    "api_tool",             # tools.api_tool

    # ui automation
    "ui_tool",              # tools.ui_tool
    "ui_ops",               # capabilities.ui_ops

    # memory
    "session_memory",       # memory.session_memory
    "plugin_memory",        # plugins.plugin_memory
    "episodic_memory",      # memory.episodic

    # text
    "text_ops",             # capabilities.text_ops
})


class PluginContextBuilder:
    """
    Instantiates and returns only the services a plugin declared in
    its manifest's `allowed_services` field.

    All provider methods use lazy imports — nothing is loaded unless
    a plugin actually requests it.
    """

    def __init__(self) -> None:
        # Maps service token → bound provider method
        self._providers: dict[str, Callable[[], Any]] = {
            # context / UI
            "window_context":    self._provide_window_context,
            "app_classifier":    self._provide_app_classifier,
            "app_profiler":      self._provide_app_profiler,
            "screen_reader":     self._provide_screen_reader,
            "selector_engine":   self._provide_selector_engine,
            "ui_fallback":       self._provide_ui_fallback,

            # shell / terminal
            "terminal_resolver": self._provide_terminal_resolver,
            "shell_tool":        self._provide_shell_tool,
            "process_bridge":    self._provide_process_bridge,

            # file system
            "file_tool":         self._provide_file_tool,
            "file_ops":          self._provide_file_ops,
            "smart_file_patcher": self._provide_smart_file_patcher,

            # web / api
            "web_ops":           self._provide_web_ops,
            "api_tool":          self._provide_api_tool,

            # ui automation
            "ui_tool":           self._provide_ui_tool,
            "ui_ops":            self._provide_ui_ops,

            # memory
            "session_memory":    self._provide_session_memory,
            "plugin_memory":     self._provide_plugin_memory,
            "episodic_memory":   self._provide_episodic_memory,

            # text
            "text_ops":          self._provide_text_ops,
        }

    # ── Public API ────────────────────────────────────────────────────────────

    def build(self, allowed_services: list[str]) -> dict[str, Any]:
        """
        Build and return a scoped context dict for the given service list.

        Gate order (stops at first failure per service):
          1. permission_guard.check_services()  — risk-level gate (HIGH/FORBIDDEN)
          2. permission_checker.check_service_access() — OS/env gate (admin, restricted)
          3. provider()                          — instantiate the service object

        Only services that pass both gates are provisioned.
        Unknown tokens are logged as warnings and skipped.

        Returns:
            A dict mapping service token → ready-to-use object (or callable
            for live-snapshot services like window_context).
        """
        # ── Gate 1: risk-level check ──────────────────────────────────────────
        # check_services() publishes confirmation_required / task_failed events
        # for HIGH/FORBIDDEN services and returns the blocked subset.
        try:
            from safety.permission_guard import permission_guard
            _, blocked_svcs, _ = permission_guard.check_services(
                plugin_name="plugin",       # caller can override via build_for()
                allowed_services=allowed_services,
            )
        except Exception as exc:
            logger.warning(
                "PluginContextBuilder: permission_guard unavailable (%s) — "
                "proceeding without risk gate.", exc
            )
            blocked_svcs = []

        # ── Gate 2: OS/env check ──────────────────────────────────────────────
        try:
            from context.permission_checker import permission_checker as _pc
            env_checker = _pc
        except Exception as exc:
            logger.warning(
                "PluginContextBuilder: permission_checker unavailable (%s) — "
                "skipping OS gate.", exc
            )
            env_checker = None

        ctx: dict[str, Any] = {}

        for svc in allowed_services:
            # Unknown token — skip entirely
            if svc not in self._providers:
                logger.warning(
                    "PluginContextBuilder: unknown service '%s' requested — "
                    "skipping. Add it to VALID_SERVICES and _providers if "
                    "this is a new service.", svc
                )
                continue

            # Blocked by permission_guard (HIGH / FORBIDDEN)
            if svc in blocked_svcs:
                logger.warning(
                    "PluginContextBuilder: service '%s' blocked by permission_guard "
                    "— not provisioned.", svc
                )
                ctx[svc] = None
                continue

            # Blocked by OS / environment
            if env_checker is not None:
                ok, reason = env_checker.check_service_access(svc)
                if not ok:
                    logger.warning(
                        "PluginContextBuilder: service '%s' denied by "
                        "permission_checker: %s — not provisioned.", svc, reason
                    )
                    ctx[svc] = None
                    continue

            # All gates passed — provision the service
            try:
                ctx[svc] = self._providers[svc]()
                logger.debug("PluginContextBuilder: provisioned '%s'", svc)
            except Exception as exc:
                logger.warning(
                    "PluginContextBuilder: failed to provision '%s': %s — "
                    "plugin will receive None for this service.", svc, exc
                )
                ctx[svc] = None

        return ctx

    def build_for(
        self,
        plugin_name: str,
        allowed_services: list[str],
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Named variant of build() that forwards plugin_name and task_id to
        permission_guard so event payloads carry proper identification.

        Use this from sandbox_runner and executor when the plugin name
        and task_id are known.
        """
        try:
            from safety.permission_guard import permission_guard
            _, blocked_svcs, _ = permission_guard.check_services(
                plugin_name=plugin_name,
                allowed_services=allowed_services,
                task_id=task_id,
            )
        except Exception as exc:
            logger.warning(
                "PluginContextBuilder.build_for: permission_guard unavailable "
                "(%s) — proceeding without risk gate.", exc
            )
            blocked_svcs = []

        try:
            from context.permission_checker import permission_checker as _pc
            env_checker = _pc
        except Exception as exc:
            logger.warning(
                "PluginContextBuilder.build_for: permission_checker unavailable "
                "(%s) — skipping OS gate.", exc
            )
            env_checker = None

        ctx: dict[str, Any] = {}

        for svc in allowed_services:
            if svc not in self._providers:
                logger.warning(
                    "PluginContextBuilder: unknown service '%s' — skipping.", svc
                )
                continue

            if svc in blocked_svcs:
                logger.warning(
                    "PluginContextBuilder: '%s' blocked by permission_guard.", svc
                )
                ctx[svc] = None
                continue

            if env_checker is not None:
                ok, reason = env_checker.check_service_access(svc)
                if not ok:
                    logger.warning(
                        "PluginContextBuilder: '%s' denied by permission_checker: %s",
                        svc, reason,
                    )
                    ctx[svc] = None
                    continue

            try:
                ctx[svc] = self._providers[svc]()
                logger.debug("PluginContextBuilder: provisioned '%s'", svc)
            except Exception as exc:
                logger.warning(
                    "PluginContextBuilder: failed to provision '%s': %s", svc, exc
                )
                ctx[svc] = None

        return ctx

    def validate_services(self, allowed_services: list[str]) -> list[str]:
        """
        Returns a list of warning strings for unknown service tokens.
        Called by plugin_validator to catch manifest typos before execution.
        """
        warnings: list[str] = []
        for svc in allowed_services:
            if svc not in VALID_SERVICES:
                warnings.append(
                    f"Unknown service '{svc}' in allowed_services. "
                    f"Valid tokens: {sorted(VALID_SERVICES)}"
                )
        return warnings

    # ── Providers ─────────────────────────────────────────────────────────────
    # Each returns a ready-to-use object OR a zero-arg callable (for services
    # that should be fetched fresh on every call, e.g. window_context).

    # ── context / UI ──────────────────────────────────────────────────────────

    def _provide_window_context(self) -> Callable[[], dict]:
        """
        Returns a zero-arg callable so plugins always get a fresh snapshot:
            win = service_ctx["window_context"]()
        """
        from context.window_detector import WindowDetector
        from context.focus_tracker import FocusTracker

        wd = WindowDetector()
        ft = FocusTracker()

        def get_window() -> dict:
            return {
                "active_window": wd.get_active_window(),
                "focused_app":   ft.get_focused_app(),
                "window_title":  wd.get_window_title(),
                "window_rect":   wd.get_window_rect(),   # {x, y, w, h}
            }

        return get_window

    def _provide_app_classifier(self):
        from context.app_classifier import AppClassifier
        return AppClassifier()

    def _provide_app_profiler(self):
        from context.app_profiler import AppProfiler
        return AppProfiler()

    def _provide_screen_reader(self):
        from automation.screen_reader import ScreenReader
        return ScreenReader()

    def _provide_selector_engine(self):
        from automation.selector_engine import SelectorEngine
        return SelectorEngine()

    def _provide_ui_fallback(self):
        from automation.ui_fallback import UIFallback
        return UIFallback()

    # ── shell / terminal ──────────────────────────────────────────────────────

    def _provide_terminal_resolver(self):
        from core.terminal_resolver import TerminalResolver
        return TerminalResolver()

    def _provide_shell_tool(self):
        from tools.shell_tool import ShellTool
        return ShellTool()

    def _provide_process_bridge(self):
        from tools.process_bridge import ProcessBridge
        return ProcessBridge()

    # ── file system ───────────────────────────────────────────────────────────

    def _provide_file_tool(self):
        from tools.file_tool import FileTool
        return FileTool()

    def _provide_file_ops(self):
        from capabilities.file_ops import FileOps
        return FileOps()

    def _provide_smart_file_patcher(self):
        from tools.smart_file_patcher import SmartFilePatcher
        return SmartFilePatcher()

    # ── web / api ─────────────────────────────────────────────────────────────

    def _provide_web_ops(self):
        from capabilities.web_ops import WebOps
        return WebOps()

    def _provide_api_tool(self):
        from tools.api_tool import ApiTool
        return ApiTool()

    # ── ui automation ─────────────────────────────────────────────────────────

    def _provide_ui_tool(self):
        from tools.ui_tool import UITool
        return UITool()

    def _provide_ui_ops(self):
        from capabilities.ui_ops import UIOps
        return UIOps()

    # ── memory ────────────────────────────────────────────────────────────────

    def _provide_session_memory(self):
        from memory.session_memory import SessionMemory
        return SessionMemory()

    def _provide_plugin_memory(self):
        from plugins.plugin_memory import PluginMemory
        return PluginMemory()

    def _provide_episodic_memory(self):
        from memory.episodic import episodic_memory
        return episodic_memory

    # ── text ──────────────────────────────────────────────────────────────────

    def _provide_text_ops(self):
        from capabilities.text_ops import TextOps
        return TextOps()


# ── Global singleton ──────────────────────────────────────────────────────────
# Matches the singleton pattern used throughout your codebase
# (sandbox_runner = SandboxRunner(), episodic_memory = EpisodicMemory(), etc.)

plugin_context_builder = PluginContextBuilder()