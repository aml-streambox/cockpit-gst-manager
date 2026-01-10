# cockpit-gst-manager - D-Bus API Specification

## Service Details

| Property | Value |
|----------|-------|
| Service Name | `org.cockpit.GstManager` |
| Object Path | `/org/cockpit/GstManager` |
| Interface | `org.cockpit.GstManager1` |

---

## Methods

### Instance Management

#### ListInstances

Get all configured instances.

| | Type | Description |
|-|------|-------------|
| **Returns** | `a{sv}[]` | Array of instance objects |

**Example Response:**
```json
[
  {
    "id": "hdmi-srt-01",
    "name": "HDMI to SRT",
    "status": "running",
    "pipeline": "v4l2src device=/dev/vdin1 ! ..."
  }
]
```

---

#### CreateInstance

Create a new pipeline instance.

| | Type | Description |
|-|------|-------------|
| **name** | `s` | Display name |
| **pipeline** | `s` | GStreamer CLI command |
| **Returns** | `s` | Instance ID |

---

#### DeleteInstance

Remove an instance (must be stopped).

| | Type | Description |
|-|------|-------------|
| **instance_id** | `s` | Instance ID |
| **Returns** | `b` | Success |

---

#### StartInstance

Start a pipeline.

| | Type | Description |
|-|------|-------------|
| **instance_id** | `s` | Instance ID |
| **Returns** | `b` | Success |

---

#### StopInstance

Stop a running pipeline.

| | Type | Description |
|-|------|-------------|
| **instance_id** | `s` | Instance ID |
| **Returns** | `b` | Success |

---

#### GetInstanceStatus

Get detailed status of an instance.

| | Type | Description |
|-|------|-------------|
| **instance_id** | `s` | Instance ID |
| **Returns** | `a{sv}` | Status object |

**Example Response:**
```json
{
  "status": "running",
  "pid": 1234,
  "uptime": 3600,
  "recording": true,
  "error": null
}
```

---

#### UpdatePipeline

Update pipeline CLI (instance must be stopped).

| | Type | Description |
|-|------|-------------|
| **instance_id** | `s` | Instance ID |
| **pipeline** | `s` | New pipeline CLI |
| **Returns** | `b` | Success |

---

### Dynamic Control

#### ToggleRecording

Enable/disable recording on a running pipeline.

| | Type | Description |
|-|------|-------------|
| **instance_id** | `s` | Instance ID |
| **enable** | `b` | Enable recording |
| **location** | `s` | Optional: storage path |
| **Returns** | `b` | Success |

---

### Discovery

#### GetBoardContext

Get current hardware discovery information.

| | Type | Description |
|-|------|-------------|
| **Returns** | `s` | JSON string with board context |

**Example Response:**
```json
{
  "video_inputs": [
    {"device": "/dev/vdin1", "type": "hdmi-in", "available": true}
  ],
  "custom_plugins": [
    {"name": "aml_h264enc", "type": "HW H.264 encoder"}
  ],
  "storage": [
    {"path": "/mnt/sdcard", "available": true, "free_gb": 32}
  ]
}
```

---

### AI Integration

#### AiGeneratePipeline

Generate a pipeline from natural language prompt.

| | Type | Description |
|-|------|-------------|
| **prompt** | `s` | User's request |
| **provider** | `s` | Optional: AI provider name |
| **Returns** | `s` | Generated pipeline CLI |

---

#### AiFixError

Analyze error and suggest fix.

| | Type | Description |
|-|------|-------------|
| **pipeline** | `s` | Failed pipeline CLI |
| **error** | `s` | Error message |
| **Returns** | `s` | Fixed pipeline CLI |

---

### Configuration

#### GetAiProviders

Get configured AI providers.

| | Type | Description |
|-|------|-------------|
| **Returns** | `a{sv}[]` | Array of provider configs |

---

#### AddAiProvider

Add a new AI provider.

| | Type | Description |
|-|------|-------------|
| **name** | `s` | Provider name |
| **url** | `s` | API endpoint |
| **api_key** | `s` | API key |
| **model** | `s` | Model name |
| **Returns** | `b` | Success |

---

#### RemoveAiProvider

Remove an AI provider.

| | Type | Description |
|-|------|-------------|
| **name** | `s` | Provider name |
| **Returns** | `b` | Success |

---

### Import/Export

#### ExportInstance

Export instance configuration.

| | Type | Description |
|-|------|-------------|
| **instance_id** | `s` | Instance ID |
| **Returns** | `s` | JSON configuration |

---

#### ImportInstance

Import instance configuration.

| | Type | Description |
|-|------|-------------|
| **config_json** | `s` | JSON configuration |
| **Returns** | `s` | New instance ID |

---

## Signals

#### InstanceStatusChanged

Emitted when an instance status changes.

| | Type | Description |
|-|------|-------------|
| **instance_id** | `s` | Instance ID |
| **status** | `s` | New status |

---

#### HdmiSignalChanged

Emitted when HDMI input signal changes.

| | Type | Description |
|-|------|-------------|
| **available** | `b` | Signal available |
| **resolution** | `s` | e.g., "1080p60" |

---

## Error Codes

| Code | Description |
|------|-------------|
| `InstanceNotFound` | Instance ID doesn't exist |
| `InstanceRunning` | Cannot modify running instance |
| `InstanceStopped` | Cannot perform action on stopped instance |
| `PipelineError` | GStreamer pipeline failed |
| `AiError` | AI provider request failed |
| `InvalidConfig` | Configuration validation failed |
