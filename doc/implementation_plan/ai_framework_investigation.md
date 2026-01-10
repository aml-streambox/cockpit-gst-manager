# AI Agent Framework Investigation

## Overview

Evaluating whether to use an AI agent framework for `cockpit-gst-manager` to enable:
1. **Tool Calling** - AI can discover devices, check storage, query board info
2. **Knowledge Base** - GStreamer docs, A311D2 plugin documentation

---

## Options Comparison

| Approach | Memory | Complexity | Tool Calling | Knowledge Base | Recommendation |
|----------|--------|------------|--------------|----------------|----------------|
| **No Framework** | ~0 MB | Low | Manual implementation | System prompt | ✅ Best for embedded |
| **Mirascope** | ~5 MB | Low | Built-in | Manual | ✅ Lightweight option |
| **LangChain** | ~100+ MB | High | Built-in | RAG support | ❌ Too heavy |
| **LlamaIndex** | ~80+ MB | Medium | Via agent | RAG native | ❌ Too heavy |
| **Semantic Kernel** | ~50 MB | Medium | Plugin system | Via plugins | ⚠️ Maybe |

---

## Recommended Approach: No Framework + Custom Tools

For embedded devices with <1GB memory budget, **implement tool calling manually** using the OpenAI-compatible API specification.

### Why Skip Frameworks?

1. **Memory**: LangChain/LlamaIndex add 80-150MB RAM
2. **Dependencies**: Dozens of transitive deps, complex Yocto packaging
3. **Simplicity**: Our use case is focused (GStreamer pipelines only)
4. **Control**: Direct API gives full control over behavior

---

## Tool Calling Implementation

Most LLM APIs (OpenAI, GLM, Claude, Qwen) support **function calling** natively.

### Define Tools (JSON Schema)

```python
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_board_info",
            "description": "Get current hardware status: video inputs, encoders, storage",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function", 
        "function": {
            "name": "list_video_devices",
            "description": "List available video input devices (HDMI-In, USB cameras)",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_storage",
            "description": "Check available storage locations and free space",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Optional: specific path to check"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_encoder_info",
            "description": "Get hardware encoder capabilities and parameters",
            "parameters": {
                "type": "object",
                "properties": {
                    "encoder": {"type": "string", "enum": ["h264", "h265"]}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "validate_pipeline",
            "description": "Validate a GStreamer pipeline syntax without running it",
            "parameters": {
                "type": "object",
                "properties": {
                    "pipeline": {"type": "string", "description": "GStreamer pipeline to validate"}
                },
                "required": ["pipeline"]
            }
        }
    }
]
```

### Tool Execution Flow

```
User: "Stream HDMI-In to SRT at 20Mbps"
    │
    ▼
┌─────────────────────────────────────┐
│ LLM receives prompt + tools schema  │
│ LLM decides to call: get_board_info │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ Manager executes get_board_info()   │
│ Returns: {"vdin1": available, ...}  │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ LLM receives tool result            │
│ Generates: gst-launch-1.0 ...       │
└─────────────────────────────────────┘
```

---

## Knowledge Base Options

### Option A: Embedded in System Prompt (Recommended)

**Pros:** Simple, no extra memory, works offline
**Cons:** Limited by context window (~16K-128K tokens)

```python
SYSTEM_PROMPT = """
You are a GStreamer expert for Amlogic A311D2 TVPro.

## Available Hardware Encoders

### aml_h264enc (H.264 Hardware Encoder)
Properties:
- bitrate: 1000000-100000000 (default: 10000000)
- profile: baseline|main|high (default: high)
- level: auto|1.0|...|5.2
- gop: 1-300 (default: 30)
- bframes: 0-3 (default: 0)

Example:
```
v4l2src device=/dev/vdin1 ! video/x-raw,format=NV12 ! aml_h264enc bitrate=20000000 profile=high ! h264parse ! mpegtsmux ! srtsink uri=srt://0.0.0.0:5000?mode=listener
```

### aml_h265enc (H.265 Hardware Encoder)
Properties:
- bitrate: 1000000-80000000
- profile: main|main10
...

### amlge2d (Hardware 2D Processor)
Capabilities:
- Scaling
- Format conversion
- Overlay/composition
- Rotation

Example (scale + encode):
```
v4l2src ! amlge2d ! video/x-raw,width=1280,height=720 ! aml_h264enc ! ...
```

## Common Pipeline Patterns

### HDMI-In to SRT Stream
```
v4l2src device=/dev/vdin1 ! video/x-raw,format=NV12 ! queue ! tee name=t ! queue ! aml_h264enc bitrate=20000000 ! h264parse ! mpegtsmux ! srtsink uri=srt://0.0.0.0:5000?mode=listener
```

### With Dynamic Recording
```
... tee name=t t. ! queue ! srtsink t. ! queue ! splitmuxsink location=/mnt/sdcard/rec%05d.ts max-size-time=60000000000
```
"""
```

### Option B: Local RAG (Future Enhancement)

For larger documentation, use local vector search:

1. **Embed docs** at build time (GStreamer plugin docs, A311D2 specs)
2. **Store in SQLite** with sentence-transformers embeddings
3. **Query on-demand** before sending to LLM

**Memory cost:** +20-50MB
**Recommendation:** Defer to Phase 5; start with system prompt

---

## Proposed Tools for cockpit-gst-manager

| Tool | Description | Data Source |
|------|-------------|-------------|
| `get_board_info` | Full hardware context | `discovery.py` |
| `list_video_devices` | Available cameras/inputs | `/dev/video*`, `/sys/class/video4linux` |
| `check_storage` | Storage paths + free space | `os.statvfs()` |
| `get_encoder_info` | Encoder capabilities | Static knowledge |
| `validate_pipeline` | Check GST syntax | `gst-launch-1.0 --parse` |
| `get_gst_element_info` | Element properties | `gst-inspect-1.0` |

---

## Implementation Plan

### Phase 1: Manual Tool Calling
- Define tool schemas in `ai/tools.py`
- Implement tool execution handlers
- Include in LLM API requests
- Handle tool call responses

### Phase 2: Knowledge Base (System Prompt)
- Document all A311D2 GStreamer plugins
- Document common pipeline patterns
- Include in system prompt

### Phase 3: Advanced (Optional)
- Local RAG with SQLite + embeddings
- Dynamic doc retrieval

---

## Memory Estimate

| Component | Without Framework | With LangChain |
|-----------|-------------------|----------------|
| Backend base | 50 MB | 50 MB |
| AI module | 5 MB | 100+ MB |
| Knowledge base | 0 MB (prompt) | 50 MB (RAG) |
| **Total** | **~55 MB** | **~200+ MB** |

---

## Conclusion

**Recommendation: No external framework**

1. Implement tool calling using native API support (all major LLMs support it)
2. Embed GStreamer/A311D2 knowledge in system prompt
3. Defer RAG to future phase if needed

This keeps memory usage minimal while providing full AI capabilities.
