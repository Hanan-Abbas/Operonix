"""Auto-generated tests for plugin: auto_clicker_plugin
Intent: auto clicker
"""
from __future__ import annotations

import asyncio
import sys
import os

# sys.path bootstrap — find project root 3 levels up from tests/
_tests_dir    = os.path.abspath(os.path.dirname(__file__))
_plugin_dir   = os.path.dirname(_tests_dir)
_project_root = os.path.dirname(os.path.dirname(_plugin_dir))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import pytest

# Import plugin relative to its own directory
sys.path.insert(0, _plugin_dir)
from plugin import AutoClickerPlugin


@pytest.fixture
def plugin():
    return AutoClickerPlugin()


@pytest.fixture
def base_context():
    return {"active_window": "test", "app_type": "test"}


# ── Attribute tests ──────────────────────────────────────────────────────────

def test_plugin_has_required_attributes(plugin):
    assert hasattr(plugin, "name") and plugin.name
    assert hasattr(plugin, "description") and plugin.description
    assert hasattr(plugin, "version") and plugin.version
    assert hasattr(plugin, "run")
    assert hasattr(plugin, "validate")
    assert asyncio.iscoroutinefunction(plugin.run)


def test_plugin_name_is_correct(plugin):
    assert plugin.name == "auto_clicker_plugin"


# ── Validate tests ───────────────────────────────────────────────────────────

def test_validate_returns_none_for_valid_args(plugin):
    result = plugin.validate({"click_interval": 0.1, "click_count": 3})
    assert result is None


def test_validate_returns_error_for_missing_args(plugin):
    result = plugin.validate({})
    assert result is not None
    assert "click_interval" in result or "click_count" in result


def test_validate_returns_error_for_missing_click_count(plugin):
    result = plugin.validate({"click_interval": 0.5})
    assert result is not None


def test_validate_returns_error_for_missing_click_interval(plugin):
    result = plugin.validate({"click_count": 3})
    assert result is not None


def test_validate_returns_error_for_invalid_interval_type(plugin):
    result = plugin.validate({"click_interval": "fast", "click_count": 3})
    assert result is not None


def test_validate_returns_error_for_invalid_count_type(plugin):
    result = plugin.validate({"click_interval": 0.1, "click_count": "three"})
    assert result is not None


# ── Run tests ────────────────────────────────────────────────────────────────

def test_run_returns_dict_with_status(plugin, base_context):
    result = asyncio.run(plugin.run(base_context, {"click_interval": 0.01, "click_count": 1}))
    assert isinstance(result, dict), "run() must return a dict"
    assert "status" in result, "result must have a 'status' key"
    assert result["status"] in ("success", "error"), (
        f"status must be 'success' or 'error', got: {result['status']}"
    )


def test_run_handles_empty_args_gracefully(plugin, base_context):
    """Plugin must not raise — return error dict instead."""
    result = asyncio.run(plugin.run(base_context, {}))
    assert isinstance(result, dict)
    assert "status" in result
    assert result["status"] == "error"


def test_run_handles_invalid_args_gracefully(plugin, base_context):
    """Negative click_count should return error, not crash."""
    result = asyncio.run(plugin.run(base_context, {"click_interval": 0.1, "click_count": -1}))
    assert isinstance(result, dict)
    assert result["status"] == "error"


def test_run_returns_intent_field(plugin, base_context):
    result = asyncio.run(plugin.run(base_context, {"click_interval": 0.01, "click_count": 1}))
    assert "intent" in result