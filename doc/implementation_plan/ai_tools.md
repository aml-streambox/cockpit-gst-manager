# AI Tool Calling Specification

## Overview

This document defines the tools available to the AI agent for discovering board information and building GStreamer pipelines.

Implementation: Native LLM function calling (no external framework)

---

## Tool Definitions

### 1. get_board_info

Get complete hardware discovery information.

```json
{
  "type": "function",
  "function": {
    "name": "get_board_info",
    "description": "Get current hardware status including video inputs, encoders, storage, and custom plugins",
    "parameters": {
      "type": "object",
      "properties": {}
    }
  }
}
```

**Returns:**
```json
{
  "video_inputs": [
    {"device": "/dev/vdin1", "type": "hdmi-in", "available": true, "signal": "1080p60"},
    {"device": "/dev/video0", "type": "usb-cam", "available": false}
  ],
  "audio_inputs": [
    {"device": "hw:0,0", "type": "hdmi-audio", "available": true}
  ],
  "encoders": ["aml_h264enc", "aml_h265enc"],
  "custom_plugins": ["amlge2d", "amlvenc"],
  "storage": [
    {"path": "/mnt/sdcard", "available": true, "free_gb": 32.5},
    {"path": "/data", "available": true, "free_gb": 4.2}
  ]
}
```

---

### 2. list_video_devices

List available video input devices with details.

```json
{
  "type": "function",
  "function": {
    "name": "list_video_devices",
    "description": "List all video input devices (HDMI-In, USB cameras) with their capabilities",
    "parameters": {
      "type": "object",
      "properties": {}
    }
  }
}
```

**Returns:**
```json
[
  {
    "device": "/dev/vdin1",
    "name": "HDMI-In",
    "type": "hdmi-in",
    "available": true,
    "current_signal": "1920x1080@60",
    "formats": ["NV12", "NV21", "YUYV"]
  }
]
```

---

### 3. check_storage

Check storage availability and free space.

```json
{
  "type": "function",
  "function": {
    "name": "check_storage",
    "description": "Check available storage locations and their free space",
    "parameters": {
      "type": "object",
      "properties": {
        "path": {
          "type": "string",
          "description": "Optional: specific path to check. If not provided, checks all known locations"
        }
      }
    }
  }
}
```

**Returns:**
```json
[
  {"path": "/mnt/sdcard", "mounted": true, "free_gb": 32.5, "total_gb": 64.0},
  {"path": "/data", "mounted": true, "free_gb": 4.2, "total_gb": 8.0}
]
```

---

### 4. get_encoder_info

Get hardware encoder capabilities and parameters.

```json
{
  "type": "function",
  "function": {
    "name": "get_encoder_info",
    "description": "Get detailed information about hardware encoders including supported properties",
    "parameters": {
      "type": "object",
      "properties": {
        "encoder": {
          "type": "string",
          "enum": ["h264", "h265", "all"],
          "description": "Which encoder to query"
        }
      }
    }
  }
}
```

**Returns:**
```json
{
  "aml_h264enc": {
    "codec": "H.264",
    "max_resolution": "4096x2160",
    "max_bitrate": 100000000,
    "properties": {
      "bitrate": {"type": "int", "min": 1000000, "max": 100000000, "default": 10000000},
      "profile": {"type": "enum", "values": ["baseline", "main", "high"], "default": "high"},
      "gop": {"type": "int", "min": 1, "max": 300, "default": 30},
      "bframes": {"type": "int", "min": 0, "max": 3, "default": 0}
    }
  }
}
```

---

### 5. get_gst_element_info

Get GStreamer element properties (uses gst-inspect).

```json
{
  "type": "function",
  "function": {
    "name": "get_gst_element_info",
    "description": "Get properties and pads for a GStreamer element",
    "parameters": {
      "type": "object",
      "properties": {
        "element": {
          "type": "string",
          "description": "GStreamer element name (e.g., srtsink, aml_h264enc)"
        }
      },
      "required": ["element"]
    }
  }
}
```

**Returns:** Parsed output from `gst-inspect-1.0 <element>`

---

### 6. validate_pipeline

Validate GStreamer pipeline syntax without running.

```json
{
  "type": "function",
  "function": {
    "name": "validate_pipeline",
    "description": "Check if a GStreamer pipeline is syntactically valid",
    "parameters": {
      "type": "object",
      "properties": {
        "pipeline": {
          "type": "string", 
          "description": "The gst-launch-1.0 pipeline to validate"
        }
      },
      "required": ["pipeline"]
    }
  }
}
```

**Returns:**
```json
{
  "valid": true,
  "elements": ["v4l2src", "aml_h264enc", "srtsink"],
  "warnings": []
}
```
or
```json
{
  "valid": false,
  "error": "no element 'invalid_element'",
  "suggestion": "Did you mean 'aml_h264enc'?"
}
```

---

### 7. get_running_instances

Get status of all running GStreamer instances.

```json
{
  "type": "function",
  "function": {
    "name": "get_running_instances",
    "description": "List all running GStreamer pipeline instances and their status",
    "parameters": {
      "type": "object",
      "properties": {}
    }
  }
}
```

---

## Tool Execution Flow

```
┌──────────────────────────────────────────────────────────────┐
│  User: "Stream HDMI at 15Mbps to SRT listener on port 5000"  │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  AI Agent receives: prompt + tool schemas                    │
│  AI decides: "I need to check if HDMI-In is available"       │
│  AI returns: {"tool_calls": [{"name": "get_board_info"}]}    │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  Manager executes: get_board_info()                          │
│  Result: {"video_inputs": [{"device": "/dev/vdin1", ...}]}   │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  AI receives tool result                                     │
│  AI generates pipeline:                                      │
│  "v4l2src device=/dev/vdin1 ! aml_h264enc bitrate=15000000   │
│   ! h264parse ! mpegtsmux ! srtsink uri=srt://..."           │
└──────────────────────────────────────────────────────────────┘
```

---

## Implementation Location

```
backend/ai/
├── agent.py         # Main AI interaction
├── providers.py     # Multi-provider LLM support
└── tools.py         # Tool definitions and handlers
```

---

## API Request Format

```python
# Example request to LLM API with tools
response = requests.post(
    provider_url,
    headers={"Authorization": f"Bearer {api_key}"},
    json={
        "model": model_name,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "tools": TOOLS,
        "tool_choice": "auto"
    }
)
```
