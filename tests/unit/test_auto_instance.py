"""Regression tests for auto HDMI instance restart behavior."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from auto_instance import AutoInstanceConfig, AutoInstanceManager, CaptureSource, OutputCodec, OutputTransport, PipelineBuilder
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


def test_prepare_recording_path_creates_parent_dir(tmp_path):
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
    config = AutoInstanceConfig(
        recording_enabled=True,
        recording_path=str(tmp_path / "captures" / "session01.ts"),
    )

    manager._prepare_recording_path(config)

    assert config.recording_path.endswith("session01.ts")
    assert (tmp_path / "captures").is_dir()


def test_prepare_recording_path_appends_timestamp_filename_for_directory(tmp_path, monkeypatch):
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
    monkeypatch.setattr("auto_instance.time.strftime", lambda *_args, **_kwargs: "20260403-173000")
    config = AutoInstanceConfig(
        recording_enabled=False,
        recording_path=str(tmp_path / "captures") + "/",
    )

    manager._prepare_recording_path(config)

    assert config.recording_path.endswith("captures/capture-20260403-173000.ts")


def test_pipeline_builder_applies_gop_preset():
    config = AutoInstanceConfig(
        capture_source=CaptureSource.VFMCAP,
        use_hdr=False,
        gop_pattern=7,
        bitrate_kbps=12000,
        rc_mode=1,
        framerate=60,
    )

    builder = PipelineBuilder()
    builder._supports_full_fixed_qp = lambda: True
    pipeline = builder.build(config)

    assert "gop-pattern=7" in pipeline
    assert "bitrate=12000" in pipeline
    assert "rc-mode=1" in pipeline


def test_pipeline_builder_applies_lossless_mode():
    config = AutoInstanceConfig(
        capture_source=CaptureSource.VFMCAP,
        use_hdr=True,
        lossless_enable=True,
        gop_pattern=4,
        framerate=60,
    )

    builder = PipelineBuilder()
    builder._supports_full_fixed_qp = lambda: True
    pipeline = builder.build(config)
    encoder_section = pipeline.split('! video/x-h265', 1)[0]

    assert "lossless-enable=true" in pipeline
    assert "gop-pattern=4" in pipeline
    assert "bitrate=" not in encoder_section
    assert "rc-mode=" not in encoder_section


def test_pipeline_builder_supports_h264_output():
    config = AutoInstanceConfig(
        capture_source=CaptureSource.VFMCAP,
        output_codec=OutputCodec.H264,
        use_hdr=False,
    )

    builder = PipelineBuilder()
    builder._supports_full_fixed_qp = lambda: True
    pipeline = builder.build(config)

    assert "video/x-h264" in pipeline
    assert "h264parse config-interval=-1" in pipeline


def test_pipeline_builder_supports_srt_wait_for_connection():
    config = AutoInstanceConfig(
        capture_source=CaptureSource.VFMCAP,
        use_hdr=False,
        output_transport=OutputTransport.SRT,
        srt_wait_for_connection=True,
    )

    builder = PipelineBuilder()
    pipeline = builder.build(config)

    assert 'srtsink uri="srt://:8888" wait-for-connection=true latency=600 sync=false' in pipeline


def test_pipeline_builder_supports_rtmp_output():
    config = AutoInstanceConfig(
        capture_source=CaptureSource.VFMCAP,
        use_hdr=False,
        output_codec=OutputCodec.H264,
        output_transport=OutputTransport.RTMP,
        rtmp_url="rtmp://example.com/live/test",
    )

    builder = PipelineBuilder()
    pipeline = builder.build(config)

    assert 'flvmux name=mux streamable=true ! rtmpsink location="rtmp://example.com/live/test"' in pipeline
    assert 'video/x-h264,stream-format=avc' in pipeline


def test_pipeline_builder_supports_rtsp_output():
    config = AutoInstanceConfig(
        capture_source=CaptureSource.VFMCAP,
        use_hdr=False,
        output_transport=OutputTransport.RTSP,
        rtsp_url="rtsp://example.com:8554/live/test",
    )

    builder = PipelineBuilder()
    pipeline = builder.build(config)

    assert 'rtspclientsink name=rtsp location="rtsp://example.com:8554/live/test" protocols=tcp' in pipeline
    assert 'rtph264pay' not in pipeline
    assert 'rtpmp4apay' not in pipeline
    assert 'h265parse config-interval=-1 ! queue' in pipeline
    assert 'aacparse ! queue' in pipeline


def test_pipeline_builder_rejects_recording_with_non_srt_output():
    config = AutoInstanceConfig(
        capture_source=CaptureSource.VFMCAP,
        use_hdr=False,
        output_transport=OutputTransport.RTMP,
        output_codec=OutputCodec.H264,
        recording_enabled=True,
    )

    builder = PipelineBuilder()

    with pytest.raises(ValueError, match="Recording is currently supported only with SRT output"):
        builder.build(config)


def test_pipeline_builder_applies_fixed_qp_value_in_cqp_mode():
    config = AutoInstanceConfig(
        capture_source=CaptureSource.VFMCAP,
        output_codec=OutputCodec.H265,
        use_hdr=False,
        rc_mode=2,
        fixed_qp_value=23,
    )

    builder = PipelineBuilder()
    builder._supports_full_fixed_qp = lambda: True
    pipeline = builder.build(config)
    encoder_section = pipeline.split('! video/x-h265', 1)[0]

    assert "rc-mode=2" in encoder_section
    assert "qp-i=23" in encoder_section
    assert "qp-p=23" in encoder_section
    assert "qp-b=23" in encoder_section


def test_pipeline_builder_falls_back_to_qp_b_when_plugin_lacks_qp_i_p():
    config = AutoInstanceConfig(
        capture_source=CaptureSource.VFMCAP,
        rc_mode=2,
        fixed_qp_value=19,
    )

    builder = PipelineBuilder()
    builder._supports_full_fixed_qp = lambda: False
    pipeline = builder.build(config)
    encoder_section = pipeline.split('! video/x-h265', 1)[0]

    assert "qp-b=19" in encoder_section
    assert "qp-i=" not in encoder_section
    assert "qp-p=" not in encoder_section
