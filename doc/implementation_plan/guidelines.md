# cockpit-gst-manager - Project Guidelines

## Code Standards

### Python (Backend)

- **Version:** Python 3.8+
- **Style:** PEP 8
- **Type hints:** Required for public functions
- **Docstrings:** Google style

```python
def start_instance(instance_id: str) -> bool:
    """Start a GStreamer pipeline instance.
    
    Args:
        instance_id: Unique identifier for the instance.
        
    Returns:
        True if started successfully, False otherwise.
    """
```

### JavaScript (Frontend)

- **Style:** ES6+, no TypeScript (keep it simple for embedded)
- **No frameworks:** Vanilla JS + cockpit.js only
- **Naming:** camelCase for functions, UPPER_CASE for constants

```javascript
const REFRESH_INTERVAL = 5000;

function updateInstanceStatus(instanceId) {
    // ...
}
```

### CSS

- **Framework:** PatternFly 4 (Cockpit native)
- **Custom styles:** Prefix with `gst-` (e.g., `.gst-instance-card`)
- **Avoid:** Inline styles, !important

---

## Git Workflow

### Branches

| Branch | Purpose |
|--------|---------|
| `main` | Stable releases |
| `dev` | Development integration |
| `feature/*` | New features |
| `fix/*` | Bug fixes |

### Commit Messages

Format: `<type>: <description>`

Types:
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation
- `refactor:` Code refactoring
- `test:` Tests
- `chore:` Build/config changes

Example:
```
feat: add dynamic recording toggle
fix: handle HDMI disconnect gracefully
docs: update API specification
```

---

## File Naming

| Type | Convention | Example |
|------|------------|---------|
| Python modules | snake_case | `discovery.py` |
| JavaScript | kebab-case | `ai-chat.js` |
| Documentation | kebab-case | `api-spec.md` |
| Config files | lowercase | `config.json` |

---

## Configuration Files

### Instance JSON

Required fields:
```json
{
  "id": "string (unique)",
  "name": "string (display name)",
  "pipeline": "string (gst-launch-1.0 command)"
}
```

Optional fields:
```json
{
  "autostart": false,
  "trigger_event": null,
  "recording": {
    "enabled": false,
    "location": "/mnt/sdcard/recordings/"
  }
}
```

### AI Provider JSON

```json
{
  "name": "Provider Name",
  "url": "https://api.example.com/v1/chat/completions",
  "api_key": "xxx",
  "model": "model-name"
}
```

---

## Error Handling

### Backend

- Use exceptions for errors
- Log all errors with context
- Return structured error responses via D-Bus

```python
import logging

logger = logging.getLogger(__name__)

try:
    start_pipeline(instance_id)
except PipelineError as e:
    logger.error(f"Failed to start {instance_id}: {e}")
    raise DBusError("StartFailed", str(e))
```

### Frontend

- Show user-friendly error messages
- Log details to console for debugging
- Use Cockpit's notification system

```javascript
cockpit.spawn(["gst-manager", "start", id])
    .fail(function(error) {
        console.error("Start failed:", error);
        showNotification("error", "Failed to start pipeline");
    });
```

---

## Security Rules

1. **API keys:** Stored in `/var/lib/gst-manager/config.json` (root-only readable)
2. **No shell injection:** Always use arrays for subprocess calls
3. **Validate inputs:** Check pipeline strings before execution
4. **D-Bus auth:** Cockpit handles authentication

```python
# GOOD
subprocess.run(["gst-launch-1.0"] + pipeline_args)

# BAD - shell injection risk
subprocess.run(f"gst-launch-1.0 {pipeline}", shell=True)
```

---

## Testing

### Unit Tests

Location: `tests/`

```bash
python -m pytest tests/
```

### Manual Testing

1. Build Yocto image with recipe
2. Deploy to target hardware
3. Access Cockpit at `https://<device-ip>:9090`
4. Navigate to GStreamer Manager

---

## Dependencies

### Runtime

| Package | Purpose |
|---------|---------|
| python3 | Backend runtime |
| python3-dbus | D-Bus bindings |
| gstreamer1.0 | Pipeline execution |
| cockpit | Web interface |

### Development

| Package | Purpose |
|---------|---------|
| pytest | Unit testing |
| pylint | Linting |

---

## Memory Guidelines

**Target:** <100MB for daemon

- Avoid loading entire files into memory
- Use streaming for AI responses
- Clean up subprocess handles
- Limit history files in memory (load on demand)

---

## Adding New Features

1. Create issue/task describing the feature
2. Update `overview.md` if architecture changes
3. Update `api.md` if D-Bus interface changes
4. Implement with tests
5. Update README if user-facing
