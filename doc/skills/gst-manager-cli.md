# gst-manager-cli Skill

`gst-manager-cli` is the preferred control surface for AI agents that need to
operate the Cockpit GStreamer Manager without manually crafting D-Bus calls.

## Why Use It

- JSON-first output for reliable machine parsing
- Dedicated commands for common workflows
- Generic `call` command for full D-Bus coverage
- Safe for automation and regression scripts

## Core Usage

```bash
gst-manager-cli instances list
gst-manager-cli instances status <instance-id>
gst-manager-cli instances start <instance-id>
gst-manager-cli instances stop <instance-id>
gst-manager-cli instances logs <instance-id> --lines 100
```

## UVC Workflows

List devices:

```bash
gst-manager-cli uvc list
```

Preview the validated UVC transcode pipeline:

```bash
gst-manager-cli uvc preview /dev/video0 \
  --format h264 \
  --encoder h265 \
  --bitrate-kbps 4000 \
  --output-type srt \
  --port 8889
```

Create and start a UVC instance immediately:

```bash
gst-manager-cli uvc create uvc-c920 /dev/video0 \
  --format h264 \
  --encoder h265 \
  --bitrate-kbps 4000 \
  --output-type srt \
  --port 8889 \
  --start
```

## Auto HDMI Workflows

Get current config:

```bash
gst-manager-cli auto get
```

Preview config from JSON file:

```bash
gst-manager-cli auto preview --config-file auto-config.json
```

Apply config:

```bash
gst-manager-cli auto set --config-file auto-config.json
```

## AI Workflows

```bash
gst-manager-cli ai providers
gst-manager-cli ai generate "Create a 4K60 HDMI HEVC SRT pipeline" --provider local
gst-manager-cli ai fix-error "<pipeline>" "<error text>"
```

## Full Escape Hatch

If a dedicated command does not exist yet, call the D-Bus method directly:

```bash
gst-manager-cli call GetBoardContext
gst-manager-cli call StartInstance 1234abcd
```

## Agent Guidance

1. Prefer dedicated subcommands over raw `call`.
2. Assume output is JSON unless `--raw` is requested.
3. Use `uvc preview` before `uvc create` when adjusting formats or output modes.
4. Use `instances logs` immediately after failures.
5. Use `call` only when you need a method that is not yet wrapped.
