# cockpit-gst-manager

A Cockpit plugin for managing GStreamer streaming/encoding pipelines on Amlogic A311D2 (T6/T7) TVPro.

## Platform

| Component | Version |
|-----------|---------|
| SoC | Amlogic A311D2 |
| GStreamer | 1.20.7 |
| Cockpit | 220 (PatternFly 4) |
| Python | 3.10+ |

## Features

- **Multi-instance** - Run multiple GStreamer pipelines simultaneously
- **AI Assistant** - Natural language to GStreamer CLI (BYO API key)
- **Manual Editor** - Direct CLI editing for advanced users
- **Dynamic Control** - Toggle recording while streaming
- **Event Triggers** - Auto-start on HDMI signal, USB plug, boot
- **Video Compositor** - OSD/overlay via ge2d hardware acceleration
- **Import/Export** - Share pipeline configurations
- **Localization** - English and Chinese UI

## Documentation

See [doc/implementation_plan/](doc/implementation_plan/) for:

- [Overview](doc/implementation_plan/overview.md) - Architecture and phases
- [Hardware Reference](doc/implementation_plan/hardware_reference.md) - VDIN, encoders, audio
- [API Specification](doc/implementation_plan/api.md) - D-Bus interface
- [AI Tools](doc/implementation_plan/ai_tools.md) - Tool calling spec
- [Operations](doc/implementation_plan/operations.md) - Debugging and logs

## Quick Start

```bash
# On target device
systemctl start gst-manager
# Access via Cockpit at https://<device-ip>:9090
```

## Sample Configurations

See [samples/](samples/) for working pipeline examples.

## Requirements

- Amlogic A311D2/T7 TVPro hardware
- Yocto-built image with Cockpit
- GStreamer 1.20+ with Amlogic plugins

## License

MIT

