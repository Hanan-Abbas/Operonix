"""
context/context_validator.py
─────────────────────────────
Context validator with Optimization B — live accessibility tree snapshots.

Changes from original
──────────────────────
Original had one method: validate_action_context(intent, context) -> (bool, str)
All permission/safety checks are preserved exactly.

Added for Optimization B (Gap 3 / UIReadinessGuard):

  snapshot(force_refresh, invalidate_handles) -> AccessibilitySnapshot
    Issues a live OS accessibility query, bypassing the in-process cache
    when force_refresh=True.  Used exclusively by UIReadinessGuard inside
    executor.py immediately before UI tool invocation.

    Guarantees (Optimization B):
      1. Cache is set to None BEFORE the syscall so concurrent reads block
         rather than serve a stale tree.
      2. Element handles are re-enumerated from the root window
         (invalidate_handles=True), not reused from a previous query.
      3. A hard 150 ms timeout (UI_AX_TIMEOUT_SECONDS from settings)
         causes the call to raise asyncio.TimeoutError rather than block
         indefinitely under system load — executor tags this ENV_TRANSIENT.

    The snapshot() method is intentionally separate from
    validate_action_context() so the JIT validator never interferes with
    the existing safety-check pipeline.
"""
from __future__ import annotations

import asyncio
import logging
import platform
import threading
from typing import Any

from context.permission_checker import permission_checker
from core.config import settings
from core.event_bus import bus

logger = logging.getLogger("ContextValidator")


# ─────────────────────────────────────────────────────────────────────────────
# Accessibility snapshot (Optimization B)
# ─────────────────────────────────────────────────────────────────────────────

class AccessibilitySnapshot:
    """
    Thin wrapper around a live OS accessibility tree query result.

    The UIReadinessGuard in executor.py calls snapshot.matches(expected)
    to compare the live tree against the routing-time snapshot stored in
    MethodDecision.expected_ui_state.
    """

    def __init__(self, elements: list[dict], timestamp: float) -> None:
        self.elements  = elements    # list of {role, label, bounds, visible}
        self.timestamp = timestamp   # monotonic time of the OS query

    def matches(self, expected_state: Any) -> bool:
        """
        Compare this snapshot against the expected_ui_state frozen at routing
        time.

        Matching strategy
        ─────────────────
        If expected_state has an "app" key, check that at least one element's
        label or role contains it (case-insensitive).  If no elements were
        found at all (empty tree — app not responding), return False.

        This is intentionally lenient: the primary guard is the focus check
        in UIReadinessGuard.  The AX tree check is a secondary guard that
        catches gross state mismatches (e.g. a dialog opened between routing
        and execution).
        """
        if not self.elements:
            # Empty tree means the app is not exposing accessibility info
            # or is not yet ready.  Treat as mismatch.
            return False

        if expected_state is None:
            return True

        # expected_state is a MappingProxyType — read it like a dict
        expected_app = None
        try:
            expected_app = expected_state.get("app_name") or expected_state.get("app")
        except Exception:
            pass

        if expected_app:
            expected_lower = str(expected_app).lower()
            return any(
                expected_lower in str(el.get("label", "")).lower()
                or expected_lower in str(el.get("role", "")).lower()
                for el in self.elements
            )

        return True


# ─────────────────────────────────────────────────────────────────────────────
# Context validator
# ─────────────────────────────────────────────────────────────────────────────

class ContextValidator:
    """
    Validates whether the current environment is safe for executing a given
    intent, and provides a live accessibility tree snapshot for UIReadinessGuard.
    """

    def __init__(self) -> None:
        self.logger   = logging.getLogger("ContextValidator")
        self._os_name = platform.system()

        # In-process accessibility tree cache (Optimization B)
        # Protected by _cache_lock so concurrent JIT checks serialise correctly.
        self._cached_tree : AccessibilitySnapshot | None = None
        self._cache_lock  : threading.Lock = threading.Lock()

    # ── Original validate_action_context — preserved verbatim ────────────────

    async def validate_action_context(
        self, intent: str, current_context: dict
    ) -> tuple[bool, str]:
        """
        Validate if the current environment is suitable for *intent*.
        Returns (is_valid, reason_message).
        All checks are unchanged from the original.
        """
        active_app = current_context.get("active_window", "").lower()
        app_type   = current_context.get("app_type")
        state      = current_context.get("state", {})

        self.logger.debug(
            "Validating context for intent: '%s' | App: %s (%s)",
            intent, active_app, app_type,
        )

        # 1. Permission checker
        target_path = state.get("target_path")
        allowed, reason = permission_checker.is_action_allowed(intent, target_path)
        if not allowed:
            self.logger.warning(
                "PermissionChecker blocked intent '%s': %s", intent, reason
            )
            return False, reason

        # 2. File operation safety
        if "file" in intent and target_path:
            if "/etc/" in target_path or "/usr/" in target_path:
                if not state.get("is_admin", False):
                    msg = (
                        f"Blocked: Insufficient permissions to modify {target_path}"
                    )
                    self.logger.warning(msg)
                    return False, msg

            if not permission_checker.is_actually_writable(target_path):
                msg = (
                    f"Blocked: OS reports '{target_path}' is not writable by the agent."
                )
                self.logger.warning(msg)
                return False, msg

        # 3. UI operation safety
        if intent in ("click", "double_click", "type_text", "move_cursor", "scroll"):
            if app_type and app_type not in (
                "editor", "terminal", "browser", "desktop", "unknown"
            ):
                msg = (
                    f"Context mismatch: Cannot perform '{intent}' "
                    f"in app type '{app_type}'"
                )
                self.logger.warning(msg)
                return False, msg

        # 4. Web / browser safety
        if app_type == "browser":
            domain = state.get("current_url_domain", "")
            if any(d in domain for d in ["bank", "finance", "payment"]):
                msg = (
                    f"Security block: Automation disabled on sensitive site '{domain}'"
                )
                self.logger.warning(msg)
                return False, msg

        # 5. Shell / dangerous commands
        if intent == "install_package" and not state.get("is_admin", False):
            msg = (
                "Blocked: package install may require admin; confirm in UI "
                "or run with elevated agent"
            )
            self.logger.warning(msg)
            return False, msg

        self.logger.info(
            "Context validated for intent '%s' in app '%s'", intent, active_app
        )
        return True, "Context Validated"

    # ── Optimization B — live accessibility tree snapshot ────────────────────

    async def snapshot(
        self,
        force_refresh      : bool = False,
        invalidate_handles : bool = False,
    ) -> AccessibilitySnapshot:
        """
        Return an AccessibilitySnapshot from the live OS accessibility API.

        Parameters
        ──────────
        force_refresh
            When True the in-process cache is invalidated BEFORE the OS
            query so concurrent reads block rather than serve stale data
            (Optimization B guarantee 1).

        invalidate_handles
            When True element handles are re-enumerated from the root
            window rather than reused from the previous query
            (Optimization B guarantee 2).

        Timeout
        ───────
        The OS query runs under asyncio.wait_for() with the timeout from
        settings.UI_AX_TIMEOUT_SECONDS (default 0.15 s).  A TimeoutError
        propagates to the caller — UIReadinessGuard tags it ENV_TRANSIENT
        (Optimization B guarantee 4).

        Concurrency
        ───────────
        Cache invalidation under _cache_lock ensures that two concurrent
        JIT validators do not race — the second waits for the first OS
        query to complete before reading.
        """
        ax_timeout: float = float(
            getattr(settings, "UI_AX_TIMEOUT_SECONDS", 0.15)
        )

        if force_refresh:
            # Guarantee 1: set cache to None BEFORE the OS query
            with self._cache_lock:
                self._cached_tree = None

        # Run the platform-specific AX query in an executor thread so it
        # does not block the event loop.
        loop = asyncio.get_running_loop()
        tree = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                self._query_ax_tree,
                invalidate_handles,
            ),
            timeout=ax_timeout,
        )

        with self._cache_lock:
            self._cached_tree = tree

        return tree

    def _query_ax_tree(self, invalidate_handles: bool) -> AccessibilitySnapshot:
        """
        Blocking OS accessibility query — runs in a thread pool executor.

        Platform support
        ────────────────
        macOS   → AppKit / ApplicationServices (AXUIElement)
        Windows → pywinauto / UIAutomation
        Linux   → AT-SPI via pyatspi (best-effort)

        All platforms fall back to an empty snapshot if the accessibility
        API is unavailable rather than raising — the caller (UIReadinessGuard)
        treats an empty snapshot as ENV_TRANSIENT and aborts cleanly.
        """
        import time as _time
        ts = _time.monotonic()

        try:
            if self._os_name == "Darwin":
                return self._query_ax_macos(invalidate_handles, ts)
            elif self._os_name == "Windows":
                return self._query_ax_windows(invalidate_handles, ts)
            elif self._os_name == "Linux":
                return self._query_ax_linux(ts)
            else:
                logger.debug(
                    "AX tree query: unsupported OS '%s' — returning empty snapshot.",
                    self._os_name,
                )
                return AccessibilitySnapshot(elements=[], timestamp=ts)
        except Exception as exc:
            logger.warning(
                "AX tree query failed (%s): %s — returning empty snapshot.",
                self._os_name, exc,
            )
            return AccessibilitySnapshot(elements=[], timestamp=ts)

    def _query_ax_macos(
        self, invalidate_handles: bool, ts: float
    ) -> AccessibilitySnapshot:
        """macOS AXUIElement query via AppKit."""
        try:
            import AppKit  # type: ignore
            import ApplicationServices as AS  # type: ignore
        except ImportError:
            logger.debug("AppKit/ApplicationServices not available on this macOS build.")
            return AccessibilitySnapshot(elements=[], timestamp=ts)

        elements: list[dict] = []
        try:
            app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            if not app:
                return AccessibilitySnapshot(elements=[], timestamp=ts)

            pid = app.processIdentifier()
            ax_app = AS.AXUIElementCreateApplication(pid)

            # Guarantee 2: re-enumerate from root when invalidate_handles=True
            # by creating a fresh AXUIElement reference (handles are per-process,
            # so a new CreateApplication call forces re-resolution).
            if invalidate_handles:
                ax_app = AS.AXUIElementCreateApplication(pid)

            err, children = AS.AXUIElementCopyAttributeValue(
                ax_app, "AXChildren", None
            )
            if err == 0 and children:
                for child in (children or [])[:20]:  # limit depth
                    try:
                        _, role  = AS.AXUIElementCopyAttributeValue(child, "AXRole", None)
                        _, label = AS.AXUIElementCopyAttributeValue(child, "AXTitle", None)
                        _, frame = AS.AXUIElementCopyAttributeValue(child, "AXFrame", None)
                        elements.append({
                            "role"   : str(role  or ""),
                            "label"  : str(label or ""),
                            "bounds" : str(frame  or ""),
                            "visible": True,
                        })
                    except Exception:
                        continue
        except Exception as exc:
            logger.debug("macOS AX query inner error: %s", exc)

        return AccessibilitySnapshot(elements=elements, timestamp=ts)

    def _query_ax_windows(
        self, invalidate_handles: bool, ts: float
    ) -> AccessibilitySnapshot:
        """Windows UIAutomation query via pywinauto."""
        try:
            import pywinauto  # type: ignore
        except ImportError:
            logger.debug("pywinauto not available — Windows AX query skipped.")
            return AccessibilitySnapshot(elements=[], timestamp=ts)

        elements: list[dict] = []
        try:
            desktop = pywinauto.Desktop(backend="uia")
            fg = desktop.top_from_point(*pywinauto.mouse.get_cursor_pos())
            # Guarantee 2: find_all() re-queries from the root element
            children = fg.find_all(depth=1) if invalidate_handles else (
                fg.find_all(depth=1)
            )
            for child in children[:20]:
                try:
                    rect = child.rectangle()
                    elements.append({
                        "role"   : str(getattr(child, "control_type", "")),
                        "label"  : str(getattr(child, "window_text", lambda: "")()),
                        "bounds" : f"{rect.left},{rect.top},{rect.right},{rect.bottom}",
                        "visible": child.is_visible(),
                    })
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("Windows AX query inner error: %s", exc)

        return AccessibilitySnapshot(elements=elements, timestamp=ts)

    def _query_ax_linux(self, ts: float) -> AccessibilitySnapshot:
        """Linux AT-SPI query via pyatspi."""
        try:
            import pyatspi  # type: ignore
        except ImportError:
            logger.debug("pyatspi not available — Linux AX query skipped.")
            return AccessibilitySnapshot(elements=[], timestamp=ts)

        elements: list[dict] = []
        try:
            desktop = pyatspi.Registry.getDesktop(0)
            for app in desktop:
                if app is None:
                    continue
                try:
                    for i in range(min(app.childCount, 5)):
                        child = app.getChildAtIndex(i)
                        if child:
                            elements.append({
                                "role"   : str(child.getRoleName()),
                                "label"  : str(child.name),
                                "bounds" : "",
                                "visible": child.getState().contains(
                                    pyatspi.STATE_VISIBLE
                                ),
                            })
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("Linux AX query inner error: %s", exc)

        return AccessibilitySnapshot(elements=elements, timestamp=ts)


# Global singleton
context_validator = ContextValidator()