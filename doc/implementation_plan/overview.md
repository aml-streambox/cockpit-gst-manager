# cockpit-gst-manager - Implementation Overview

## Project Summary

A Cockpit plugin for Amlogic A311D2 (T6) TVPro that manages multiple GStreamer streaming/encoding pipelines with AI-assisted command generation.

### Key Features

| Feature | Description |
|---------|-------------|
| Multi-instance | Run multiple GStreamer pipelines simultaneously |
| AI Assistant | Natural language → GStreamer CLI generation |
| Manual Editor | Direct CLI editing for advanced users |
| Dynamic Control | Toggle recording while streaming |
| Event Triggers | Auto-start pipelines on HDMI signal, USB plug, boot |
| Video Compositor | OSD/overlay via ge2d hardware acceleration |
| Import/Export | Share pipeline configurations |

---

## Technology Stack

| Component | Technology |
|-----------|------------|
| Backend | Python 3 + asyncio |
| Frontend | Vanilla JS + cockpit.js |
| UI Theme | Cockpit native (PatternFly) |
| IPC | D-Bus |
| Process Mgmt | systemd |
| AI | Generic LLM API (user-provided) |
| Config | JSON files |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Cockpit Web UI                           │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────┐ │
│  │  Dashboard   │ │ AI Chat      │ │ Pipeline Editor      │ │
│  └──────────────┘ └──────────────┘ └──────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                           │ D-Bus
┌─────────────────────────────────────────────────────────────┐
│                  gst-manager Daemon                         │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌─────────────┐ │
│  │ Instances │ │ Discovery │ │  Events   │ │ AI Agent    │ │
│  └───────────┘ └───────────┘ └───────────┘ └─────────────┘ │
└─────────────────────────────────────────────────────────────┘
                           │
┌─────────────────────────────────────────────────────────────┐
│              GStreamer Pipelines                            │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────┐ │
│  │ HDMI→SRT     │ │ USB→RTMP     │ │ Compositor           │ │
│  └──────────────┘ └──────────────┘ └──────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## Directory Structure

```
cockpit-gst-manager/
├── README.md
├── LICENSE
├── doc/
│   └── implementation_plan/
│       ├── overview.md              # This file
│       ├── guidelines.md            # Project rules
│       └── api.md                   # D-Bus API spec
├── backend/
│   ├── main.py                      # Entry point
│   ├── discovery.py                 # Hardware scanning
│   ├── events.py                    # HDMI/udev monitoring
│   ├── instances.py                 # GStreamer process mgmt
│   ├── dynamic.py                   # Runtime modifications
│   ├── history.py                   # JSON file management
│   ├── api.py                       # D-Bus interface
│   └── ai/
│       ├── agent.py                 # LLM interaction
│       └── providers.py             # Multi-provider support
├── frontend/
│   ├── manifest.json                # Cockpit metadata
│   ├── index.html                   # Entry point
│   ├── gst-manager.js               # Main logic
│   ├── ai-chat.js                   # Chat interface
│   ├── pipeline-editor.js           # CLI editor
│   └── gst-manager.css              # Styling
├── yocto/
│   ├── cockpit-gst-manager_1.0.bb   # Yocto recipe
│   └── files/
│       └── gst-manager.service      # systemd service
└── tests/
    └── ...
```

---

## Runtime Layout

```
/usr/lib/gst-manager/                # Backend Python
/usr/share/cockpit/gst-manager/      # Frontend
/var/lib/gst-manager/
├── config.json                      # Settings + AI providers
├── board_context.json               # Discovery cache
└── instances/
    └── {instance-id}/
        ├── current.json             # Active config
        └── history/
            └── {timestamp}.json     # Backups
```

---

## Implementation Phases

### Phase 1: Core (Priority: High)
- [ ] Backend skeleton with D-Bus server
- [ ] Instance CRUD (create/start/stop/delete)
- [ ] Board discovery module
- [ ] Basic Cockpit UI dashboard
- [ ] Manual pipeline editor
- [ ] Yocto recipe + systemd service

### Phase 2: Events & History
- [ ] HDMI signal detection integration
- [ ] Autostart on boot
- [ ] Event trigger system
- [ ] JSON history storage
- [ ] Import/export functionality

### Phase 3: Dynamic Control
- [ ] Recording toggle (tee + splitmuxsink)
- [ ] Runtime status monitoring
- [ ] Error log capture and display

### Phase 4: AI Integration
- [ ] Generic LLM API client
- [ ] Multi-provider configuration UI
- [ ] Pipeline generation from prompts
- [ ] Error analysis and fix suggestions
- [ ] 3-retry logic

### Phase 5: Advanced Features
- [ ] Video compositor presets
- [ ] MCP integration (optional)
- [ ] Pipeline snapshots (optional)

---

## Memory Budget

Target: <1GB for management (GStreamer pipelines excluded)

| Component | Estimated RAM |
|-----------|---------------|
| Cockpit core | ~80 MB |
| gst-manager daemon | ~50-80 MB |
| **Total** | **~130-160 MB** |

---

## Related Documents

- [guidelines.md](./guidelines.md) - Project rules and coding standards
- [api.md](./api.md) - D-Bus API specification
