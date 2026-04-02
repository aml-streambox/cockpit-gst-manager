"""Tests for gst-manager CLI client."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from cli_client import main_async


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    async def call(self, method, *args):
        self.calls.append((method, args))
        value = self.responses.get(method)
        if callable(value):
            return value(*args)
        if value is None:
            return "{}"
        return value


@pytest.mark.asyncio
async def test_instances_list_outputs_json(capsys):
    client = FakeClient({"ListInstances": json.dumps([{"id": "abcd1234", "name": "demo"}])})

    rc = await main_async(["instances", "list"], client=client)

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["id"] == "abcd1234"
    assert client.calls == [("ListInstances", ())]


@pytest.mark.asyncio
async def test_uvc_preview_converts_kbps_to_bps_and_passes_output_config(capsys):
    client = FakeClient({"GetUVCDevicePipeline": "pipeline-text"})

    rc = await main_async([
        "uvc", "preview", "/dev/video0",
        "--format", "h264",
        "--encoder", "h265",
        "--bitrate-kbps", "4000",
        "--port", "8889",
    ], client=client)

    assert rc == 0
    assert capsys.readouterr().out.strip() == "pipeline-text"
    method, args = client.calls[0]
    assert method == "GetUVCDevicePipeline"
    assert args[0] == "/dev/video0"
    assert args[5] == "h265"
    assert args[6] == 4_000_000
    assert json.loads(args[8])["port"] == 8889


@pytest.mark.asyncio
async def test_uvc_create_with_start_starts_instance(capsys):
    client = FakeClient({
        "CreateUVCInstance": json.dumps({"instance_id": "uvc12345", "pipeline": "demo"}),
        "StartInstance": True,
    })

    rc = await main_async([
        "uvc", "create", "cam1", "/dev/video0",
        "--start",
    ], client=client)

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["instance_id"] == "uvc12345"
    assert out["started"] is True
    assert client.calls[1] == ("StartInstance", ("uvc12345",))


@pytest.mark.asyncio
async def test_auto_set_reads_config_file(tmp_path, capsys):
    config_path = tmp_path / "auto.json"
    config_path.write_text(json.dumps({"capture_source": "vfmcap", "srt_port": 8888}))
    client = FakeClient({"SetAutoInstanceConfig": True})

    rc = await main_async([
        "auto", "set", "--config-file", str(config_path)
    ], client=client)

    assert rc == 0
    assert json.loads(client.calls[0][1][0])["srt_port"] == 8888
    assert capsys.readouterr().out.strip() == "true"


@pytest.mark.asyncio
async def test_generic_call_coerces_bool_and_int_arguments(capsys):
    client = FakeClient({"SetInstanceAutostart": True})

    rc = await main_async([
        "call", "SetInstanceAutostart", "abcd1234", "true", "5"
    ], client=client)

    assert rc == 0
    assert client.calls[0] == ("SetInstanceAutostart", ("abcd1234", True, 5))
    assert capsys.readouterr().out.strip() == "true"
