# AI Coding Context - cockpit-gst-manager

This file MUST be read and followed by any AI assistant generating code for this project.

---

## Critical Rules

### 1. Documentation Must Stay Updated

When making code changes:
- Update relevant documentation in `doc/implementation_plan/` if the change affects:
  - API signatures (update `api.md`)
  - New features or behavior (update `overview.md`)
  - AI tools or prompts (update `ai_tools.md`, `gstreamer_knowledge_base.md`)
  - Error handling (update `error_recovery.md`)
  - New translatable strings (update `localization.md`)
- Add docstrings to all public functions
- Update inline comments when logic changes
- Keep README.md in sync with major features

### 2. No Emoji

- Do not use emoji in code, comments, documentation, or UI text
- Use plain text descriptions instead
- Error messages and logs must be professional text only

### 3. Code Style

Python:
- Follow PEP 8
- Use type hints for all public functions
- Use Google-style docstrings
- Max line length: 100 characters
- Use `async/await` for I/O operations

JavaScript:
- ES6+ syntax
- No TypeScript (keep simple for embedded)
- Use `const` by default, `let` when needed
- camelCase for functions/variables
- UPPER_CASE for constants

CSS:
- Prefix custom classes with `gst-`
- Use PatternFly variables when possible
- No inline styles

### 4. Error Handling

- Never silently swallow exceptions
- Log all errors with context
- Return structured error responses
- Use error codes from `api.md`

```python
# Good
try:
    result = do_something()
except SomeError as e:
    logger.error(f"Failed to do something: {e}")
    raise DBusError("OperationFailed", str(e))

# Bad
try:
    result = do_something()
except:
    pass
```

### 5. Security

- Never use `shell=True` in subprocess calls
- Validate all user input before use in pipelines
- API keys are sensitive - never log them
- Check file paths to prevent directory traversal

```python
# Good
subprocess.run(["gst-launch-1.0", *args])

# Bad - shell injection risk
subprocess.run(f"gst-launch-1.0 {user_input}", shell=True)
```

### 6. Memory Efficiency

- This runs on embedded device with limited RAM
- Avoid loading large files entirely into memory
- Use generators/iterators for large data
- Clean up resources promptly
- Target: <100MB daemon memory

### 7. Async Design

- Main daemon is async (asyncio)
- D-Bus handlers should not block
- Use `asyncio.create_task()` for background work
- GStreamer processes are managed separately

### 8. Logging

- Use Python `logging` module
- Log levels: DEBUG, INFO, WARNING, ERROR
- Include context in log messages
- Log to journald (systemd)

```python
logger = logging.getLogger(__name__)
logger.info(f"Starting instance {instance_id}")
logger.error(f"Pipeline failed for {instance_id}: {error}")
```

### 9. Testing

- Write tests for new functionality
- Unit tests in `tests/unit/`
- Hardware tests marked with `@pytest.mark.hardware`
- Run `pytest tests/unit/ -v` before committing

### 10. Git Commits

Format: `<type>: <description>`

Types:
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation
- `refactor:` Code refactoring
- `test:` Tests
- `chore:` Build/config

Example: `feat: add HDMI signal detection`

---

## Project Structure Reference

```
cockpit-gst-manager/
├── backend/
│   ├── main.py           # Entry point, D-Bus server
│   ├── discovery.py      # Hardware detection
│   ├── events.py         # HDMI/udev monitoring
│   ├── instances.py      # GStreamer process management
│   ├── dynamic.py        # Runtime modifications
│   ├── history.py        # JSON file management
│   ├── api.py            # D-Bus interface
│   └── ai/
│       ├── agent.py      # LLM interaction
│       ├── providers.py  # Multi-provider support
│       └── tools.py      # Tool definitions
├── frontend/
│   ├── manifest.json     # Cockpit metadata
│   ├── index.html        # Entry point
│   ├── gst-manager.js    # Main logic
│   ├── ai-chat.js        # Chat interface
│   ├── pipeline-editor.js# CLI editor
│   ├── gst-manager.css   # Styling
│   └── po/               # Translations
├── yocto/
│   ├── cockpit-gst-manager_1.0.bb
│   └── files/
│       └── gst-manager.service
└── tests/
    ├── conftest.py
    ├── unit/
    └── integration/
```

---

## Key Documentation

Before implementing a feature, read:
- `doc/implementation_plan/overview.md` - Architecture
- `doc/implementation_plan/api.md` - D-Bus API
- `doc/implementation_plan/guidelines.md` - Coding standards
- `doc/implementation_plan/hardware_reference.md` - VDIN, encoders, audio

For AI-related code:
- `doc/implementation_plan/ai_tools.md` - Tool definitions
- `doc/implementation_plan/gstreamer_knowledge_base.md` - System prompt

For operations:
- `doc/implementation_plan/operations.md` - Debugging, logs, diagnostics
- `doc/implementation_plan/error_recovery.md` - Recovery strategy

Sample configs in `samples/` directory.


---

## Localization Reminder

All user-facing strings must be wrapped for translation:

Python:
```python
from gettext import gettext as _
message = _("Pipeline started successfully")
```

JavaScript:
```javascript
cockpit.gettext("Pipeline started successfully")
```

Add new strings to `doc/implementation_plan/localization.md`.

---

## Quick Checklist Before Committing

- [ ] Code follows style guidelines
- [ ] No emoji anywhere
- [ ] Public functions have type hints and docstrings
- [ ] Error handling is proper (no silent failures)
- [ ] Relevant documentation updated
- [ ] No hardcoded secrets or paths
- [ ] Tests added/updated if applicable
- [ ] Commit message follows format
