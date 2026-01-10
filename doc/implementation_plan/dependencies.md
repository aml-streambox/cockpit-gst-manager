# Python Dependencies

## Runtime Dependencies

| Package | Source | Purpose |
|---------|--------|---------|
| python3 | Yocto stdlib | Runtime |
| python3-dbus | Yocto | D-Bus interface |
| python3-json | Yocto stdlib | JSON handling |
| python3-asyncio | Yocto stdlib | Async I/O |
| python3-logging | Yocto stdlib | Logging |
| python3-aiohttp | PyPI/Yocto | HTTP client for LLM API |

## Yocto Recipe Dependencies

In `cockpit-gst-manager_1.0.bb`:

```bitbake
RDEPENDS:${PN} = " \
    python3 \
    python3-dbus \
    python3-json \
    python3-asyncio \
    python3-logging \
    python3-aiohttp \
    cockpit \
    cockpit-bridge \
    gstreamer1.0 \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
"
```

## Notes

- `asyncio`, `json`, `logging` are part of Python stdlib, included by default
- `aiohttp` may need to be added to Yocto if not present:
  - Check: `bitbake -s | grep aiohttp`
  - Alternative: Use `python3-requests` (sync) if aiohttp unavailable
- No external pip packages required at runtime

## Development Only

| Package | Purpose |
|---------|---------|
| pytest | Testing |
| pylint | Linting |

These are NOT needed on target device.
