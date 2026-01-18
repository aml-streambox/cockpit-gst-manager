"""Pytest configuration and fixtures for gst-manager tests."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_dir():
    """Create temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def instances_dir(temp_dir):
    """Create temporary instances directory."""
    instances = temp_dir / "instances"
    instances.mkdir()
    return instances


@pytest.fixture
def config_dir(temp_dir):
    """Create temporary config directory."""
    return temp_dir


@pytest.fixture
def sample_instance():
    """Sample instance configuration."""
    return {
        "id": "test-001",
        "name": "Test Stream",
        "pipeline": 'v4l2src device=/dev/vdin1 ! aml_h264enc ! srtsink uri="srt://0.0.0.0:5000"',
        "status": "stopped",
        "autostart": False,
        "trigger_event": None,
        "recovery": {
            "auto_restart": True,
            "max_retries": 3,
            "retry_delay_seconds": 5
        },
        "created_at": "2026-01-10T00:00:00Z",
        "modified_at": "2026-01-10T00:00:00Z"
    }


@pytest.fixture
def history_manager(instances_dir):
    """Create HistoryManager with temp directory."""
    from history import HistoryManager
    return HistoryManager(instances_dir)


@pytest.fixture
def discovery_manager(config_dir):
    """Create DiscoveryManager with temp directory."""
    from discovery import DiscoveryManager
    return DiscoveryManager(config_dir)


@pytest.fixture
def instance_manager(history_manager):
    """Create InstanceManager with mocked history."""
    from instances import InstanceManager
    return InstanceManager(history_manager)


@pytest.fixture
def mock_subprocess():
    """Mock asyncio.create_subprocess_exec for pipeline tests."""
    with patch("asyncio.create_subprocess_exec") as mock:
        process = AsyncMock()
        process.pid = 12345
        process.returncode = 0
        process.communicate = AsyncMock(return_value=(b"", b""))
        process.terminate = MagicMock()
        process.kill = MagicMock()
        process.wait = AsyncMock()
        mock.return_value = process
        yield mock


@pytest.fixture
def mock_gst_inspect():
    """Mock gst-inspect-1.0 subprocess calls."""
    async def fake_exec(*args, **kwargs):
        process = AsyncMock()
        if "gst-inspect-1.0" in args[0]:
            element = args[1] if len(args) > 1 else ""
            if element in ["aml_h264enc", "aml_h265enc", "amlge2d", "srtsink"]:
                process.returncode = 0
                process.communicate = AsyncMock(return_value=(
                    b"Element Properties:\n  bitrate : Target bitrate\n",
                    b""
                ))
            else:
                process.returncode = 1
                process.communicate = AsyncMock(return_value=(b"", b"not found"))
        else:
            process.returncode = 0
            process.communicate = AsyncMock(return_value=(b"", b""))
        return process
    
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        yield


@pytest.fixture
def mock_v4l2():
    """Mock v4l2-ctl subprocess calls."""
    async def fake_exec(*args, **kwargs):
        process = AsyncMock()
        if "v4l2-ctl" in args[0]:
            process.returncode = 0
            process.communicate = AsyncMock(return_value=(
                b"Pixel Format: 'NV12'\nPixel Format: 'YUYV'\n",
                b""
            ))
        return process
    
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        yield


@pytest.fixture
def board_context():
    """Sample board context data."""
    return {
        "video_inputs": [
            {
                "device": "/dev/vdin1",
                "type": "hdmi-in",
                "name": "HDMI-In",
                "available": True,
                "current_signal": "1920x1080p60hz",
                "formats": ["NV12", "NV21"]
            }
        ],
        "audio_inputs": [
            {"device": "hw:0,0", "type": "hdmi-audio", "available": True}
        ],
        "encoders": ["aml_h264enc", "aml_h265enc"],
        "custom_plugins": ["amlge2d"],
        "storage": [
            {"path": "/mnt/sdcard", "mounted": True, "available": True, "free_gb": 32.5}
        ]
    }
