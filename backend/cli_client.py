#!/usr/bin/env python3
"""AI-friendly CLI client for gst-manager."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional


SERVICE_NAME = "org.cockpit.GstManager"
OBJECT_PATH = "/org/cockpit/GstManager"
INTERFACE_NAME = "org.cockpit.GstManager1"


class GstManagerDbusClient:
    """Thin async D-Bus client for gst-manager."""

    def __init__(self) -> None:
        self._bus = None
        self._iface = None

    async def _ensure_connected(self) -> None:
        if self._iface is not None:
            return

        from dbus_next.aio import MessageBus
        from dbus_next import BusType

        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        introspection = await self._bus.introspect(SERVICE_NAME, OBJECT_PATH)
        obj = self._bus.get_proxy_object(SERVICE_NAME, OBJECT_PATH, introspection)
        self._iface = obj.get_interface(INTERFACE_NAME)

    async def call(self, method: str, *args: Any) -> Any:
        await self._ensure_connected()
        fn = None
        for proxy_method in self._proxy_method_names(method):
            if hasattr(self._iface, proxy_method):
                fn = getattr(self._iface, proxy_method)
                break
        if fn is None:
            raise AttributeError(f"No proxy method found for {method}")
        return await fn(*args)

    @staticmethod
    def _proxy_method_names(method: str) -> list[str]:
        direct = f"call_{method}"
        snake = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", method)
        snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", snake).lower()
        snake_name = f"call_{snake}"
        if snake_name == direct:
            return [direct]
        return [direct, snake_name]


def _json_or_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    if stripped[0] not in "[{\"" and stripped not in {"true", "false", "null"}:
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _emit(value: Any, raw: bool = False) -> int:
    if raw:
        if isinstance(value, str):
            print(value)
        else:
            print(json.dumps(value, indent=2, sort_keys=True))
        return 0

    parsed = _json_or_text(value)
    if isinstance(parsed, (dict, list, bool, int, float)):
        print(json.dumps(parsed, indent=2, sort_keys=True))
    else:
        print(parsed)
    return 0


def _load_text_arg(inline: Optional[str], file_path: Optional[str]) -> str:
    if inline:
        return inline
    if file_path:
        return Path(file_path).read_text()
    raise ValueError("missing required input")


def _load_json_arg(inline: Optional[str], file_path: Optional[str], default: Optional[dict] = None) -> str:
    if inline:
        json.loads(inline)
        return inline
    if file_path:
        text = Path(file_path).read_text()
        json.loads(text)
        return text
    return json.dumps(default or {})


def _uvc_output_config(args: argparse.Namespace) -> str:
    config = {}
    if getattr(args, "output_type", None) == "srt":
        config = {
            "port": args.port,
            "mode": args.mode,
            "host": getattr(args, "host", "") or "",
        }
    elif getattr(args, "output_type", None) == "rtmp":
        config = {"url": args.url}
    elif getattr(args, "output_type", None) == "file":
        config = {"path": args.path}
    return json.dumps(config)


def _coerce_call_arg(value: str) -> Any:
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    try:
        return int(value)
    except ValueError:
        return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gst-manager-cli")
    parser.add_argument("--raw", action="store_true", help="print raw output without JSON auto-decoding")

    sub = parser.add_subparsers(dest="command", required=True)

    call = sub.add_parser("call", help="invoke any D-Bus method directly")
    call.add_argument("method")
    call.add_argument("args", nargs="*")

    instances = sub.add_parser("instances", help="manage pipeline instances")
    instances_sub = instances.add_subparsers(dest="instances_cmd", required=True)
    instances_sub.add_parser("list")

    inst_create = instances_sub.add_parser("create")
    inst_create.add_argument("name")
    inst_create.add_argument("--pipeline")
    inst_create.add_argument("--pipeline-file")

    inst_status = instances_sub.add_parser("status")
    inst_status.add_argument("instance_id")

    inst_start = instances_sub.add_parser("start")
    inst_start.add_argument("instance_id")

    inst_stop = instances_sub.add_parser("stop")
    inst_stop.add_argument("instance_id")

    inst_update = instances_sub.add_parser("update")
    inst_update.add_argument("instance_id")
    inst_update.add_argument("--pipeline")
    inst_update.add_argument("--pipeline-file")

    inst_delete = instances_sub.add_parser("delete")
    inst_delete.add_argument("instance_id")

    inst_logs = instances_sub.add_parser("logs")
    inst_logs.add_argument("instance_id")
    inst_logs.add_argument("--lines", type=int, default=50)

    inst_clear = instances_sub.add_parser("clear-logs")
    inst_clear.add_argument("instance_id")

    inst_export = instances_sub.add_parser("export")
    inst_export.add_argument("instance_id")

    inst_import = instances_sub.add_parser("import")
    inst_import.add_argument("--config-json")
    inst_import.add_argument("--config-file")

    autostart = sub.add_parser("autostart", help="set per-instance autostart")
    autostart.add_argument("instance_id")
    autostart.add_argument("--enabled", required=True, choices=["true", "false"])
    autostart.add_argument("--trigger", default="")

    sub.add_parser("board-context", help="show discovered board context")
    sub.add_parser("hdmi-status", help="show HDMI input status")
    sub.add_parser("passthrough-state", help="show HDMI passthrough state")

    uvc = sub.add_parser("uvc", help="manage UVC device pipelines")
    uvc_sub = uvc.add_subparsers(dest="uvc_cmd", required=True)
    uvc_sub.add_parser("list")
    uvc_sub.add_parser("refresh")

    for cmd_name in ("preview", "create"):
        cmd = uvc_sub.add_parser(cmd_name)
        if cmd_name == "create":
            cmd.add_argument("name")
        cmd.add_argument("device_path")
        cmd.add_argument("--format", default="auto", choices=["auto", "h264", "mjpeg", "yuyv"])
        cmd.add_argument("--width", type=int, default=1920)
        cmd.add_argument("--height", type=int, default=1080)
        cmd.add_argument("--fps", type=int, default=30)
        cmd.add_argument("--encoder", default="h265", choices=["h264", "h265", "none"])
        cmd.add_argument("--bitrate-kbps", type=int, default=4000)
        cmd.add_argument("--output-type", default="srt", choices=["srt", "rtmp", "file", "display"])
        cmd.add_argument("--port", type=int, default=8889)
        cmd.add_argument("--mode", default="listener", choices=["listener", "caller"])
        cmd.add_argument("--host", default="")
        cmd.add_argument("--url", default="rtmp://localhost/live/stream")
        cmd.add_argument("--path", default="/mnt/sdcard/uvc_recording.ts")
        if cmd_name == "create":
            cmd.add_argument("--start", action="store_true", help="start the instance after creating it")

    auto = sub.add_parser("auto", help="manage auto HDMI instance config")
    auto_sub = auto.add_subparsers(dest="auto_cmd", required=True)
    auto_sub.add_parser("get")
    auto_delete = auto_sub.add_parser("delete")
    auto_delete.set_defaults(auto_delete=True)
    auto_preview = auto_sub.add_parser("preview")
    auto_preview.add_argument("--config-json")
    auto_preview.add_argument("--config-file")
    auto_set = auto_sub.add_parser("set")
    auto_set.add_argument("--config-json")
    auto_set.add_argument("--config-file")

    ai = sub.add_parser("ai", help="manage AI helpers")
    ai_sub = ai.add_subparsers(dest="ai_cmd", required=True)
    ai_sub.add_parser("providers")
    ai_add = ai_sub.add_parser("add-provider")
    ai_add.add_argument("name")
    ai_add.add_argument("url")
    ai_add.add_argument("api_key")
    ai_add.add_argument("model")
    ai_remove = ai_sub.add_parser("remove-provider")
    ai_remove.add_argument("name")
    ai_gen = ai_sub.add_parser("generate")
    ai_gen.add_argument("prompt")
    ai_gen.add_argument("--provider", default="")
    ai_fix = ai_sub.add_parser("fix-error")
    ai_fix.add_argument("pipeline")
    ai_fix.add_argument("error")

    return parser


async def run_command(args: argparse.Namespace, client: GstManagerDbusClient) -> Any:
    if args.command == "call":
        return await client.call(args.method, *[_coerce_call_arg(v) for v in args.args])

    if args.command == "instances":
        if args.instances_cmd == "list":
            return await client.call("ListInstances")
        if args.instances_cmd == "create":
            pipeline = _load_text_arg(args.pipeline, args.pipeline_file)
            return await client.call("CreateInstance", args.name, pipeline)
        if args.instances_cmd == "status":
            return await client.call("GetInstanceStatus", args.instance_id)
        if args.instances_cmd == "start":
            return await client.call("StartInstance", args.instance_id)
        if args.instances_cmd == "stop":
            return await client.call("StopInstance", args.instance_id)
        if args.instances_cmd == "update":
            pipeline = _load_text_arg(args.pipeline, args.pipeline_file)
            return await client.call("UpdatePipeline", args.instance_id, pipeline)
        if args.instances_cmd == "delete":
            return await client.call("DeleteInstance", args.instance_id)
        if args.instances_cmd == "logs":
            return await client.call("GetInstanceLogs", args.instance_id, args.lines)
        if args.instances_cmd == "clear-logs":
            return await client.call("ClearInstanceLogs", args.instance_id)
        if args.instances_cmd == "export":
            return await client.call("ExportInstance", args.instance_id)
        if args.instances_cmd == "import":
            config_json = _load_text_arg(args.config_json, args.config_file)
            return await client.call("ImportInstance", config_json)

    if args.command == "autostart":
        enabled = args.enabled == "true"
        return await client.call("SetInstanceAutostart", args.instance_id, enabled, args.trigger)

    if args.command == "board-context":
        return await client.call("GetBoardContext")
    if args.command == "hdmi-status":
        return await client.call("GetHdmiStatus")
    if args.command == "passthrough-state":
        return await client.call("GetPassthroughState")

    if args.command == "uvc":
        if args.uvc_cmd == "list":
            return await client.call("GetUVCDevices")
        if args.uvc_cmd == "refresh":
            return await client.call("RefreshUVCDevices")
        output_config = _uvc_output_config(args)
        bitrate_bps = args.bitrate_kbps * 1000
        if args.uvc_cmd == "preview":
            return await client.call(
                "GetUVCDevicePipeline",
                args.device_path,
                args.format,
                args.width,
                args.height,
                args.fps,
                args.encoder,
                bitrate_bps,
                args.output_type,
                output_config,
            )
        if args.uvc_cmd == "create":
            created = await client.call(
                "CreateUVCInstance",
                args.name,
                args.device_path,
                args.format,
                args.width,
                args.height,
                args.fps,
                args.encoder,
                bitrate_bps,
                args.output_type,
                output_config,
            )
            parsed = _json_or_text(created)
            if args.start and isinstance(parsed, dict) and parsed.get("instance_id"):
                await client.call("StartInstance", parsed["instance_id"])
                parsed["started"] = True
            return parsed

    if args.command == "auto":
        if args.auto_cmd == "get":
            return await client.call("GetAutoInstanceConfig")
        if args.auto_cmd == "delete":
            return await client.call("DeleteAutoInstance")
        config_json = _load_json_arg(args.config_json, args.config_file)
        if args.auto_cmd == "preview":
            return await client.call("GetAutoInstancePipelinePreview", config_json)
        if args.auto_cmd == "set":
            return await client.call("SetAutoInstanceConfig", config_json)

    if args.command == "ai":
        if args.ai_cmd == "providers":
            return await client.call("GetAiProviders")
        if args.ai_cmd == "add-provider":
            return await client.call("AddAiProvider", args.name, args.url, args.api_key, args.model)
        if args.ai_cmd == "remove-provider":
            return await client.call("RemoveAiProvider", args.name)
        if args.ai_cmd == "generate":
            return await client.call("AiGeneratePipeline", args.prompt, args.provider)
        if args.ai_cmd == "fix-error":
            return await client.call("AiFixError", args.pipeline, args.error)

    raise ValueError(f"Unsupported command: {args.command}")


async def main_async(argv: Optional[list[str]] = None, client: Optional[GstManagerDbusClient] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    active_client = client or GstManagerDbusClient()
    result = await run_command(args, active_client)
    return _emit(result, raw=args.raw)


def main(argv: Optional[list[str]] = None) -> int:
    try:
        return asyncio.run(main_async(argv))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
