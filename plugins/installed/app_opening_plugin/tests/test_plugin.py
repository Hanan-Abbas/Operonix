"""
Auto-generated tests for plugin: app_opening_plugin
Intent: app opening
"""
import asyncio
import pytest
import sys
import os

# Add plugin directory to path for import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plugin import AppOpeningPlugin


@pytest.fixture
def plugin():
    return AppOpeningPlugin()


@pytest.fixture
def base_context():
    return {"active_window": "test", "app_type": "test"}


def test_plugin_has_required_attributes(plugin):
    assert hasattr(plugin, "name") and plugin.name
    assert hasattr(plugin, "description") and plugin.description
    assert hasattr(plugin, "version") and plugin.version
    assert hasattr(plugin, "run")
    assert hasattr(plugin, "validate")
    assert asyncio.iscoroutinefunction(plugin.run)


def test_validate_returns_none_for_valid_args(plugin):
    result = plugin.validate({"app_name": "test_app"})
    assert result is None


def test_run_returns_dict_with_status(plugin, base_context):
    result = asyncio.run(plugin.run(base_context, {"app_name": "test_app"}))
    assert isinstance(result, dict), "run() must return a dict"
    assert "status" in result, "result must have a 'status' key"
    assert result["status"] in ("success", "error"), (
        f"status must be 'success' or 'error', got: {result['status']}"
    )


def test_run_handles_empty_args_gracefully(plugin, base_context):
    """Plugin must not crash on empty args — return error dict instead."""
    try:
        result = asyncio.run(plugin.run(base_context, {}))
        assert isinstance(result, dict)
        assert "status" in result
    except Exception as exc:
        pytest.fail(f"run() raised an exception instead of returning error dict: {exc}")


def test_run_handles_none_context(plugin):
    """Plugin must handle None or empty context gracefully."""
    try:
        result = asyncio.run(plugin.run({}, {"app_name": "test_app"}))
        assert isinstance(result, dict)
    except Exception as exc:
        pytest.fail(f"run() crashed on empty context: {exc}")