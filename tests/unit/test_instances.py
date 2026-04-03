"""Unit tests for Instance Manager."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from instances import Instance, InstanceManager, InstanceStatus, RecoveryConfig


class TestInstance:
    """Tests for Instance dataclass."""

    def test_instance_creation(self, sample_instance):
        """Test creating an instance from dict."""
        instance = Instance.from_dict(sample_instance)
        
        assert instance.id == "test-001"
        assert instance.name == "Test Stream"
        assert instance.status == InstanceStatus.STOPPED
        assert instance.recovery.auto_restart is True
        assert instance.recovery.max_retries == 3

    def test_instance_to_dict(self, sample_instance):
        """Test converting instance to dict."""
        instance = Instance.from_dict(sample_instance)
        data = instance.to_dict()
        
        assert data["id"] == "test-001"
        assert data["status"] == "stopped"
        assert isinstance(data["recovery"], dict)


class TestInstanceManager:
    """Tests for InstanceManager class."""

    @pytest.mark.asyncio
    async def test_create_instance(self, instance_manager):
        """Test creating a new instance."""
        instance_id = await instance_manager.create_instance(
            "Test Pipeline",
            "v4l2src device=/dev/vdin1 ! fakesink"
        )
        
        assert instance_id is not None
        assert len(instance_id) == 8
        assert instance_id in instance_manager.instances
        
        instance = instance_manager.instances[instance_id]
        assert instance.name == "Test Pipeline"
        assert instance.status == InstanceStatus.STOPPED

    @pytest.mark.asyncio
    async def test_delete_instance(self, instance_manager):
        """Test deleting an instance."""
        instance_id = await instance_manager.create_instance(
            "To Delete",
            "fakesrc ! fakesink"
        )
        
        result = await instance_manager.delete_instance(instance_id)
        
        assert result is True
        assert instance_id not in instance_manager.instances

    @pytest.mark.asyncio
    async def test_delete_running_instance_fails(self, instance_manager, mock_subprocess):
        """Test that deleting a running instance fails."""
        instance_id = await instance_manager.create_instance(
            "Running Instance",
            "fakesrc ! fakesink"
        )
        
        # Start the instance
        await instance_manager.start_instance(instance_id)
        
        # Attempt to delete should fail
        with pytest.raises(ValueError, match="Cannot delete running instance"):
            await instance_manager.delete_instance(instance_id)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_instance_fails(self, instance_manager):
        """Test deleting non-existent instance raises error."""
        with pytest.raises(ValueError, match="Instance not found"):
            await instance_manager.delete_instance("nonexistent")

    @pytest.mark.asyncio
    async def test_start_instance(self, instance_manager, mock_subprocess):
        """Test starting an instance."""
        instance_id = await instance_manager.create_instance(
            "Start Test",
            "fakesrc ! fakesink"
        )
        
        result = await instance_manager.start_instance(instance_id)
        
        assert result is True
        instance = instance_manager.instances[instance_id]
        assert instance.status == InstanceStatus.RUNNING
        assert instance.pid == 12345

    @pytest.mark.asyncio
    async def test_stop_instance(self, instance_manager, mock_subprocess):
        """Test stopping a running instance."""
        instance_id = await instance_manager.create_instance(
            "Stop Test",
            "fakesrc ! fakesink"
        )
        
        await instance_manager.start_instance(instance_id)
        result = await instance_manager.stop_instance(instance_id)
        
        assert result is True
        instance = instance_manager.instances[instance_id]
        assert instance.status == InstanceStatus.STOPPED
        assert instance.pid is None

    @pytest.mark.asyncio
    async def test_get_instance_status(self, instance_manager, mock_subprocess):
        """Test getting instance status."""
        instance_id = await instance_manager.create_instance(
            "Status Test",
            "fakesrc ! fakesink"
        )
        
        await instance_manager.start_instance(instance_id)
        status = instance_manager.get_instance_status(instance_id)
        
        assert status["status"] == "running"
        assert status["pid"] == 12345

    @pytest.mark.asyncio
    async def test_update_pipeline(self, instance_manager):
        """Test updating pipeline CLI."""
        instance_id = await instance_manager.create_instance(
            "Update Test",
            "fakesrc ! fakesink"
        )
        
        new_pipeline = "v4l2src device=/dev/vdin1 ! aml_h264enc ! srtsink"
        result = await instance_manager.update_pipeline(instance_id, new_pipeline)
        
        assert result is True
        assert instance_manager.instances[instance_id].pipeline == new_pipeline

    @pytest.mark.asyncio
    async def test_update_running_pipeline_fails(self, instance_manager, mock_subprocess):
        """Test that updating a running pipeline fails."""
        instance_id = await instance_manager.create_instance(
            "Running Update Test",
            "fakesrc ! fakesink"
        )
        
        await instance_manager.start_instance(instance_id)
        
        with pytest.raises(ValueError, match="Cannot update running instance"):
            await instance_manager.update_pipeline(instance_id, "new pipeline")

    @pytest.mark.asyncio
    async def test_list_instances(self, instance_manager):
        """Test listing all instances."""
        await instance_manager.create_instance("Instance 1", "pipe1")
        await instance_manager.create_instance("Instance 2", "pipe2")
        await instance_manager.create_instance("Instance 3", "pipe3")
        
        instances = instance_manager.list_instances()
        
        assert len(instances) == 3
        names = [i["name"] for i in instances]
        assert "Instance 1" in names
        assert "Instance 2" in names
        assert "Instance 3" in names

    @pytest.mark.asyncio
    async def test_stop_all(self, instance_manager, mock_subprocess):
        """Test stopping all running instances."""
        id1 = await instance_manager.create_instance("Instance 1", "pipe1")
        id2 = await instance_manager.create_instance("Instance 2", "pipe2")
        
        await instance_manager.start_instance(id1)
        await instance_manager.start_instance(id2)
        
        await instance_manager.stop_all()
        
        assert instance_manager.instances[id1].status == InstanceStatus.STOPPED
        assert instance_manager.instances[id2].status == InstanceStatus.STOPPED


class TestErrorRecovery:
    """Tests for error recovery behavior."""

    @pytest.mark.asyncio
    async def test_transient_error_detection(self, instance_manager):
        """Test detection of transient errors."""
        from instances import TRANSIENT_ERRORS
        
        # These should be detected as transient
        assert any(t in "connection refused by peer".lower() for t in TRANSIENT_ERRORS)
        assert any(t in "connection reset by peer".lower() for t in TRANSIENT_ERRORS)
        assert any(t in "operation timeout".lower() for t in TRANSIENT_ERRORS)

    @pytest.mark.asyncio
    async def test_fatal_error_detection(self, instance_manager):
        """Test detection of fatal errors."""
        from instances import FATAL_ERRORS
        
        # These should be detected as fatal
        assert any(f in "No such file or directory".lower() for f in FATAL_ERRORS)
        assert any(f in "permission denied".lower() for f in FATAL_ERRORS)
        assert any(f in "no element 'invalid'".lower() for f in FATAL_ERRORS)


class TestSignalChangeParsing:
    """Tests for hdmi-signal-change stderr parsing."""

    def test_parse_signal_change_from_stderr(self, instance_manager):
        stderr_text = (
            'message: hdmi-signal-change, reason=(string)signal-changed, '
            'width=(uint)3840, height=(uint)2160, frame-rate=(uint)5994, '
            'color-space=(string)YUV422, color-depth=(uint)10, '
            'hdr-eotf=(string)HDR10, dolby-vision=(uint)0, interlace=(uint)0;'
        )

        parsed = instance_manager._parse_signal_change(stderr_text)

        assert parsed is not None
        assert parsed.reason == "signal-changed"
        assert parsed.width == 3840
        assert parsed.height == 2160
        assert parsed.frame_rate_raw == 5994
        assert parsed.fps == 60
        assert parsed.color_depth == 10
        assert parsed.hdr_eotf == "HDR10"

    def test_parse_frame_acquire_timeout_as_signal_timeout(self, instance_manager):
        stderr_text = "Frame acquire timeout (frame 4429)\nstreaming stopped, reason error (-5)"

        parsed = instance_manager._parse_signal_change(stderr_text)

        assert parsed is not None
        assert parsed.reason == "signal-timeout"


class TestStartTimeout:
    @pytest.mark.asyncio
    async def test_start_instance_times_out_when_subprocess_hangs(self, instance_manager, monkeypatch):
        async def _hung_exec(*_args, **_kwargs):
            await asyncio.sleep(60)

        monkeypatch.setattr("asyncio.create_subprocess_exec", _hung_exec)
        instance_manager.start_command_timeout = 0.01

        instance_id = await instance_manager.create_instance(
            "Hung Start",
            "fakesrc ! fakesink"
        )

        result = await instance_manager.start_instance(instance_id)

        assert result is False
        assert instance_manager.instances[instance_id].status == InstanceStatus.ERROR
        assert "timed out" in instance_manager.instances[instance_id].error_message.lower()
