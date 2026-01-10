# Error Recovery Specification

## Strategy: Mixed Recovery

Auto-restart for transient errors, full stop for fatal errors.

---

## Error Classification

### Transient Errors (Auto-Restart)

Recoverable errors that may resolve on retry.

| Error | Cause | Recovery Action |
|-------|-------|-----------------|
| Network disconnect | SRT/RTMP connection lost | Retry up to 3 times, 5s delay |
| Stream timeout | Target not responding | Retry up to 3 times |
| Buffer underrun | Temporary resource spike | Retry immediately (1s delay) |
| Encoder hiccup | Transient HW issue | Retry up to 2 times |

**Retry parameters:**
- Max retries: 3
- Delay between retries: 5 seconds
- Backoff: None (fixed delay)

### Fatal Errors (Stop + Notify)

Non-recoverable errors requiring user intervention.

| Error | Cause | Action |
|-------|-------|--------|
| Device not found | /dev/vdin1 missing | Stop, notify user |
| HDMI signal lost | Input disconnected | Stop, wait for signal event |
| Storage full | No space for recording | Stop recording, continue streaming |
| Invalid pipeline | Syntax/element error | Stop, show error to user |
| Encoder failure | HW encoder crashed | Stop, notify user |
| Permission denied | Access issue | Stop, notify user |

---

## Recovery Flow

```
Pipeline Running
      │
      ▼
┌─────────────────┐
│  Error Detected │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     Yes    ┌─────────────────┐
│ Is Transient?   │───────────▶│ Retry (max 3)   │
└────────┬────────┘            └────────┬────────┘
         │ No                           │
         ▼                              ▼
┌─────────────────┐            ┌─────────────────┐
│ Stop Pipeline   │            │ Retry Success?  │
└────────┬────────┘            └────────┬────────┘
         │                              │
         ▼                     No       │ Yes
┌─────────────────┐     ◀───────────────┤
│ Notify User     │                     ▼
└─────────────────┘            ┌─────────────────┐
                               │ Continue Running│
                               └─────────────────┘
```

---

## Special Cases

### HDMI Signal Lost
1. Stop pipeline immediately
2. Set instance state to "waiting_signal"
3. Monitor for HDMI signal return
4. If `autostart` enabled and signal returns → auto-restart pipeline

### Storage Full (During Recording)
1. Stop recording branch only
2. Continue streaming if active
3. Notify user about recording stop
4. Do NOT auto-restart recording

### Partial Recovery
For pipelines with multiple outputs (streaming + recording):
- Try to keep working branches alive
- Only stop affected branch
- Notify user about degraded state

---

## User Notifications

| Event | Notification Level | Message |
|-------|-------------------|---------|
| Auto-retry started | Info | "Connection lost, retrying..." |
| Retry succeeded | Success | "Reconnected successfully" |
| All retries failed | Error | "Failed after 3 attempts" |
| Fatal error | Error | "{error description}" |
| Waiting for signal | Warning | "HDMI signal lost, waiting..." |
| Signal restored | Info | "HDMI signal detected" |

---

## Implementation

### Backend (instances.py)

```python
class PipelineManager:
    MAX_RETRIES = 3
    RETRY_DELAY = 5  # seconds
    
    TRANSIENT_ERRORS = [
        "connection refused",
        "connection reset",
        "timeout",
        "buffer underrun",
        "temporary failure"
    ]
    
    def is_transient_error(self, error: str) -> bool:
        return any(t in error.lower() for t in self.TRANSIENT_ERRORS)
    
    async def handle_error(self, instance_id: str, error: str):
        instance = self.instances[instance_id]
        
        if self.is_transient_error(error):
            if instance.retry_count < self.MAX_RETRIES:
                instance.retry_count += 1
                await self.emit_event("retry_started", instance_id, instance.retry_count)
                await asyncio.sleep(self.RETRY_DELAY)
                return await self.restart_pipeline(instance_id)
        
        # Fatal or max retries exceeded
        instance.status = "error"
        instance.error_message = error
        await self.emit_event("pipeline_error", instance_id, error)
```

---

## Configuration

Allow user to customize recovery behavior per instance:

```json
{
  "id": "hdmi-stream",
  "recovery": {
    "auto_restart": true,
    "max_retries": 3,
    "retry_delay": 5,
    "restart_on_signal": true
  }
}
```
