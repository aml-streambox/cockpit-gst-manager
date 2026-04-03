"""Regression tests for auto HDMI instance restart behavior."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from auto_instance import AutoInstanceConfig, AutoInstanceManager, CaptureSource
from instances import Instance, InstanceStatus, InstanceType


class DummyEventManager:
    def get_hdmi_status(self):
        return {
            "color_depth": 8,
            "hdr_info": 0,
            "signal_locked": True,
            "width": 1920,
            "height": 1080,
            "fps": 60,
        }

    def get_passthrough_state(self):
        return {"can_capture": True}


@pytest.fixture
def no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None
    monkeypatch.setattr("auto_instance.asyncio.sleep", _fake_sleep)


@pytest.mark.asyncio
async def test_passthrough_ready_recreates_running_auto_instance_on_resolution_change(no_sleep):
    history = SimpleNamespace(save_instance=AsyncMock())
    instance_manager = SimpleNamespace(
        get_instance=Mock(),
        start_instance=AsyncMock(),
        create_instance=AsyncMock(return_value="auto1234"),
        stop_instance=AsyncMock(),
        delete_instance=AsyncMock(),
        history_manager=history,
    )

    manager = AutoInstanceManager(instance_manager, event_manager=DummyEventManager())
    manager.config = AutoInstanceConfig(
        capture_source=CaptureSource.VDIN1,
        width=3840,
        height=2160,
        framerate=60,
        autostart_on_ready=True,
        use_hdr=False,
    )
    manager.instance_id = "auto1234"

    old_pipeline = manager._builder.build(manager.config)
    running_instance = Instance(
        id="auto1234",
        name="Auto HDMI Capture",
        pipeline=old_pipeline,
        instance_type=InstanceType.AUTO,
        auto_config=manager.config.to_dict(),
        status=InstanceStatus.RUNNING,
    )
    instance_manager.get_instance.return_value = running_instance

    new_instance = Instance(
        id="auto5678",
        name="Auto HDMI Capture",
        pipeline="new-pipeline",
        instance_type=InstanceType.AUTO,
        status=InstanceStatus.STOPPED,
    )

    async def _recreate(*_args, **_kwargs):
        manager.instance_id = "auto5678"
        instance_manager.get_instance.return_value = new_instance
        return "auto5678"

    recreated = AsyncMock(side_effect=_recreate)
    manager.create_or_update = recreated

    tx_status = SimpleNamespace(width=1920, height=1080, fps=60)

    await manager.on_passthrough_ready(tx_status)

    recreated.assert_awaited_once()
    instance_manager.start_instance.assert_awaited_once()


@pytest.mark.asyncio
async def test_passthrough_ready_skips_recreate_when_running_pipeline_matches(no_sleep):
    history = SimpleNamespace(save_instance=AsyncMock())
    instance_manager = SimpleNamespace(
        get_instance=Mock(),
        start_instance=AsyncMock(),
        create_instance=AsyncMock(return_value="auto1234"),
        stop_instance=AsyncMock(),
        delete_instance=AsyncMock(),
        history_manager=history,
    )

    manager = AutoInstanceManager(instance_manager, event_manager=DummyEventManager())
    manager.config = AutoInstanceConfig(
        capture_source=CaptureSource.VDIN1,
        width=3840,
        height=2160,
        framerate=60,
        autostart_on_ready=True,
        use_hdr=False,
    )
    manager.instance_id = "auto1234"

    pipeline = manager._builder.build(manager.config)
    running_instance = Instance(
        id="auto1234",
        name="Auto HDMI Capture",
        pipeline=pipeline,
        instance_type=InstanceType.AUTO,
        auto_config=manager.config.to_dict(),
        status=InstanceStatus.RUNNING,
    )
    instance_manager.get_instance.return_value = running_instance

    recreated = AsyncMock(return_value="auto5678")
    manager.create_or_update = recreated

    tx_status = SimpleNamespace(width=3840, height=2160, fps=60)

    await manager.on_passthrough_ready(tx_status)

    recreated.assert_not_awaited()
    instance_manager.start_instance.assert_not_awaited()


@pytest.mark.asyncio
async def test_hdmi_signal_ready_starts_vfmcap_without_tx_dependency(no_sleep):
    history = SimpleNamespace(save_instance=AsyncMock())
    instance_manager = SimpleNamespace(
        get_instance=Mock(),
        start_instance=AsyncMock(),
        create_instance=AsyncMock(return_value="auto1234"),
        stop_instance=AsyncMock(),
        delete_instance=AsyncMock(),
        history_manager=history,
    )

    manager = AutoInstanceManager(instance_manager, event_manager=DummyEventManager())
    manager.config = AutoInstanceConfig(
        capture_source=CaptureSource.VFMCAP,
        width=1920,
        height=1080,
        framerate=60,
        autostart_on_ready=True,
        use_hdr=False,
    )
    manager.instance_id = "auto1234"

    stopped_instance = Instance(
        id="auto1234",
        name="Auto HDMI Capture",
        pipeline="old-pipeline",
        instance_type=InstanceType.AUTO,
        auto_config=manager.config.to_dict(),
        status=InstanceStatus.STOPPED,
    )
    instance_manager.get_instance.return_value = stopped_instance

    recreated = AsyncMock(return_value="auto1234")
    manager.create_or_update = recreated

    hdmi_status = {"width": 1280, "height": 720, "fps": 60, "hdr_info": 0}

    await manager.on_hdmi_signal_ready(hdmi_status)

    recreated.assert_awaited_once()
    instance_manager.start_instance.assert_awaited_once_with("auto1234")
