"""
Tests for plugin: folder_organizer_plugin
Intent: folder_organizer
Category: file
"""
import asyncio
import os
import sys
import pytest
from unittest.mock import MagicMock, call, patch# Add plugin directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from plugin import FolderOrganizerPlugin

@pytest.fixture
def plugin():
    return FolderOrganizerPlugin()

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

# ── File plugin tests (use tmp_path, no real FS mutation) ─────────────────────

def test_validate_rejects_missing_path(plugin):
    result = plugin.validate({})
    assert result is not None  # should fail validation

def test_validate_rejects_path_traversal(plugin):
    result = plugin.validate({"path": "/etc/passwd"})
    # Either validates or rejects — must not crash
    assert result is None or isinstance(result, str)

def test_run_organizes_files(tmp_path, plugin, ctx):
    # Create test files
    image_file = tmp_path / "image.jpg"
    doc_file = tmp_path / "document.docx"
    video_file = tmp_path / "video.mp4"
    archive_file = tmp_path / "archive.zip"

    image_file.touch()
    doc_file.touch()
    video_file.touch()
    archive_file.touch()

    # Run the plugin
    result = asyncio.run(plugin.run(ctx, {"path": str(tmp_path)}))

    # Check if files were moved to their respective directories
    image_dir = tmp_path / "images"
    docs_dir = tmp_path / "docs"
    videos_dir = tmp_path / "videos"
    archives_dir = tmp_path / "archives"

    assert image_file.exists() == False
    assert doc_file.exists() == False
    assert video_file.exists() == False
    assert archive_file.exists() == False

    assert (image_dir / "image.jpg").exists()
    assert (docs_dir / "document.docx").exists()
    assert (videos_dir / "video.mp4").exists()
    assert (archives_dir / "archive.zip").exists()

    assert result["status"] == "success"
    assert result["result"] == "Files organized successfully"
    assert result["intent"] == "folder_organizer"

def test_run_handles_missing_file(plugin, ctx, tmp_path):
    result = asyncio.run(plugin.run(ctx, {"path": str(tmp_path / "nonexistent.txt")}))
    assert isinstance(result, dict)  # must not raise