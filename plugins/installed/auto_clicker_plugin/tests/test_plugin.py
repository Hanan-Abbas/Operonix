"""
Tests for plugin: auto_clicker_plugin
Intent: auto clicker
Category: background
"""
import asyncio
import os
import sys
import pytest
from unittest.mock import patch, MagicMock, call

# Add plugin directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from plugin import AutoClickerPlugin

@pytest.fixture
def plugin():
    return AutoClickerPlugin()

@pytest.fixture
def ctx():
    return {"active_window": "test_window", "app_type": "test", "app_name": "TestApp"}

# ── Structural tests (always run, no mocking needed) ─────────────────────────

def test_has_required_attributes(plugin):
    assert plugin.name
    assert plugin.description
    assert plugin.version
    assert asyncio.iscoroutinefunction(plugin.run)
    assert callable(plugin.validate)

def test_validate_returns_none_or_str(plugin):
    result = plugin.validate({})
    assert result is None or isinstance(result, str)

def test_run_always_returns_dict_with_status(plugin, ctx):
    """run() must NEVER raise — always return a dict with 'status'."""
    result = asyncio.run(plugin.run(ctx, {}))
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "status" in result, f"Missing 'status' key in {result}"
    assert result["status"] in ("success", "error"), (
        f"status must be 'success' or 'error', got {result['status']}"
    )

def test_run_never_raises_on_bad_args(plugin, ctx):
    """Plugin must handle garbage input gracefully."""
    for bad_args in [None, {}, {"x": None}, {"path": "/../../../etc/passwd"}]:
        try:
            result = asyncio.run(plugin.run(ctx, bad_args or {}))
            assert isinstance(result, dict)
        except Exception as exc:
            pytest.fail(f"run() raised {type(exc).__name__}: {exc} for args={bad_args}")

# ── Background plugin tests ───────────────────────────────────────────────────
# We do NOT use @patch("plugin.keyboard.wait") because the LLM may import
# keyboard inside run() or inside the worker function rather than at module
# level. In that case "plugin.keyboard" does not exist as a module attribute
# and @patch raises AttributeError during test SETUP (not during the test),
# which appears as a confusing mock internals traceback.
# Instead we patch at the library level and test behaviour, not internals.

def test_run_starts_and_returns_immediately(plugin, ctx):
    """run() must return a dict quickly — threads run in background."""
    import time
    start = time.monotonic()
    try:
        result = asyncio.run(plugin.run(ctx, {"interval": 0.01, "stop_hotkey": "alt+s"}))
        elapsed = time.monotonic() - start
        assert isinstance(result, dict), f"run() must return dict, got {type(result)}"
        assert "status" in result, "result must have 'status' key"
        assert result["status"] in ("success", "error"), f"bad status: {result['status']}"
        if result["status"] == "success":
            assert elapsed < 5.0, f"run() blocked {elapsed:.1f}s — must be non-blocking"
    except Exception as exc:
        pytest.fail(f"run() raised instead of returning error dict: {exc}")

def test_run_is_non_blocking(plugin, ctx):
    """run() must return in under 5 seconds regardless of background threads."""
    import time
    start = time.monotonic()
    try:
        result = asyncio.run(plugin.run(ctx, {}))
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"run() blocked {elapsed:.1f}s"
        assert isinstance(result, dict)
    except Exception as exc:
        elapsed = time.monotonic() - start
        if elapsed >= 5.0:
            pytest.fail(f"run() hung {elapsed:.1f}s before raising: {exc}")