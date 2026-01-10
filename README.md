# cockpit-gst-manager

A Cockpit plugin for managing GStreamer streaming/encoding pipelines on Amlogic A311D2 (T6) TVPro.

## Features

- **Multi-instance** - Run multiple GStreamer pipelines simultaneously
- **AI Assistant** - Natural language → GStreamer CLI generation
- **Manual Editor** - Direct CLI editing for advanced users
- **Dynamic Control** - Toggle recording while streaming
- **Event Triggers** - Auto-start on HDMI signal, USB plug, boot
- **Video Compositor** - OSD/overlay via ge2d hardware acceleration
- **Import/Export** - Share pipeline configurations

## Documentation

- [Implementation Overview](doc/implementation_plan/overview.md)
- [Project Guidelines](doc/implementation_plan/guidelines.md)
- [D-Bus API Specification](doc/implementation_plan/api.md)

## Requirements

- Python 3.8+
- Cockpit
- GStreamer 1.0
- Amlogic A311D2 TVPro (for hardware encoding)

## License

MIT
