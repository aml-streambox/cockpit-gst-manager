"""Tests for UVC pipeline generation."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from uvc_utils import FrameSize, UVCDevice, UVCPipelineBuilder, VideoFormat


def make_device() -> UVCDevice:
    return UVCDevice(
        device_path="/dev/video0",
        name="Logitech C920",
        bus_info="usb-xhci-1",
        driver="uvcvideo",
        is_uvc=True,
        formats=[
            VideoFormat(
                pixelformat="H264",
                description="H.264",
                framesizes=[FrameSize(width=1920, height=1080, min_fps=5, max_fps=30)],
            ),
            VideoFormat(
                pixelformat="MJPG",
                description="MJPEG",
                framesizes=[FrameSize(width=1920, height=1080, min_fps=5, max_fps=30)],
            ),
            VideoFormat(
                pixelformat="YUYV",
                description="YUYV",
                framesizes=[FrameSize(width=1280, height=720, min_fps=5, max_fps=30)],
            ),
        ],
    )


def test_h264_to_h265_srt_pipeline_uses_validated_amlogic_chain():
    builder = UVCPipelineBuilder(make_device())

    pipeline = builder.build_pipeline(
        format_type="h264",
        width=1920,
        height=1080,
        fps=30,
        encoder="h265",
        bitrate=4_000_000,
        output_type="srt",
        output_config={"port": 8889, "mode": "listener"},
    )

    assert 'v4l2src device=/dev/video0 io-mode=2' in pipeline
    assert 'video/x-h264,width=1920,height=1080,framerate=30/1' in pipeline
    assert 'h264parse ! amlv4l2h264dec' in pipeline
    assert 'video/x-raw,format=NV12' in pipeline
    assert 'amlvenc bitrate=4000 framerate=30 gop=1 gop-pattern=0' in pipeline
    assert 'video/x-h265 ! h265parse config-interval=-1' in pipeline
    assert 'mpegtsmux alignment=7 latency=100000000' in pipeline
    assert 'srtsink uri="srt://:8889" wait-for-connection=false sync=false' in pipeline


def test_h264_passthrough_pipeline_skips_decoder_and_encoder():
    builder = UVCPipelineBuilder(make_device())

    pipeline = builder.build_pipeline(
        format_type="h264",
        width=1920,
        height=1080,
        fps=30,
        encoder="none",
        bitrate=0,
        output_type="file",
        output_config={"path": "/tmp/uvc.ts"},
    )

    assert 'h264parse config-interval=-1' in pipeline
    assert 'amlv4l2h264dec' not in pipeline
    assert 'amlvenc' not in pipeline
    assert 'filesink location="/tmp/uvc.ts"' in pipeline


def test_mjpeg_pipeline_uses_jpegdec_videoconvert_and_amlvenc():
    builder = UVCPipelineBuilder(make_device())

    pipeline = builder.build_pipeline(
        format_type="mjpeg",
        width=1920,
        height=1080,
        fps=30,
        encoder="h264",
        bitrate=2_500_000,
        output_type="file",
        output_config={"path": "/tmp/mjpeg.ts"},
    )

    assert 'image/jpeg,width=1920,height=1080,framerate=30/1' in pipeline
    assert 'jpegdec' in pipeline
    assert 'videoconvert' in pipeline
    assert 'amlvenc bitrate=2500 framerate=30 gop=30 gop-pattern=0' in pipeline
    assert 'video/x-h264 ! h264parse config-interval=-1' in pipeline


def test_srt_caller_mode_requires_host():
    builder = UVCPipelineBuilder(make_device())

    with pytest.raises(ValueError, match="requires a host"):
        builder.build_pipeline(
            format_type="h264",
            width=1920,
            height=1080,
            fps=30,
            encoder="h265",
            bitrate=4_000_000,
            output_type="srt",
            output_config={"port": 9999, "mode": "caller"},
        )


def test_rtmp_requires_h264_encoding():
    builder = UVCPipelineBuilder(make_device())

    with pytest.raises(ValueError, match="RTMP output currently requires H.264"):
        builder.build_pipeline(
            format_type="h264",
            width=1920,
            height=1080,
            fps=30,
            encoder="h265",
            bitrate=4_000_000,
            output_type="rtmp",
            output_config={"url": "rtmp://localhost/live/test"},
        )
