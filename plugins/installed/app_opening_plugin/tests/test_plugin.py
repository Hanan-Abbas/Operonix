"""
Tests for plugin: app_opening_plugin
Intent: app opening
Category: automation
"""
import asyncio
import os
import sys
import pytest
from unittest.mock import patch, MagicMock, call

# Add plugin directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from plugin import AppOpeningPlugin

@pytest.fixture
def plugin():
    return AppOpeningPlugin()

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
    assert result is not None

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

# ── Automation plugin tests ───────────────────────────────────────────────────
# Patch at the library level ("pyautogui.click") not module level
# ("plugin.pyautogui.click") to handle both top-level and local imports.

@patch("subprocess.run", return_value=MagicMock(returncode=0))
def test_run_succeeds_with_valid_args(mock_run, plugin, ctx):
    result = asyncio.run(plugin.run(ctx, {"app_name": "test_app"}))
    assert result["status"] == "success"

@patch("subprocess.run", side_effect=Exception("display error"))
def test_run_handles_subprocess_error(mock_run, plugin, ctx):
    result = asyncio.run(plugin.run(ctx, {"app_name": "test_app"}))
    assert result["status"] == "error"
    assert "message" in result