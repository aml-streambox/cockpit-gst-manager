# Hardware and Environment Reference

## Target Platform

| Component | Version/Details |
|-----------|-----------------|
| SoC | Amlogic A311D2 (T6/T7) |
| Board | TVPro 4G/8G |
| Kernel | 5.15.x |
| GStreamer | 1.20.7 |
| Cockpit | 220 (PatternFly 4) |
| Python | 3.10+ |

---

## Video Subsystem

### VDIN Devices

| Device | Path | Purpose | Notes |
|--------|------|---------|-------|
| VDIN0 | `/dev/video0` | OSD/Graphics capture | Usually reserved for display |
| VDIN1 | `/dev/vdin1` | HDMI-In capture | Primary video input |

**VDIN1 Usage:**
```bash
# Check if available
v4l2-ctl -d /dev/vdin1 --all

# Query current signal
v4l2-ctl -d /dev/vdin1 --query-dv-timings
```

### Frame Rate Notes

| Input | Reported | Actual | Notes |
|-------|----------|--------|-------|
| 60Hz | 60fps | 60fps | Standard |
| 120Hz | 119.88fps | 120fps | Known discrepancy, use 120 in pipelines |
| VRR | Variable | Variable | Requires VRR mode enabled |

---

## GStreamer Elements

### aml_h264enc (H.264 Hardware Encoder)

```
gst-inspect-1.0 aml_h264enc
```

| Property | Type | Range | Default | Description |
|----------|------|-------|---------|-------------|
| bitrate | int | 1M-100M | 10M | Target bitrate (bits/sec) |
| profile | enum | baseline/main/high | high | H.264 profile |
| level | string | auto/1.0-5.2 | auto | H.264 level |
| gop | int | 1-300 | 30 | Keyframe interval |
| bframes | int | 0-3 | 0 | B-frame count |
| cabac | bool | true/false | true | CABAC entropy coding |

### aml_h265enc (H.265 Hardware Encoder)

| Property | Type | Range | Default | Description |
|----------|------|-------|---------|-------------|
| bitrate | int | 1M-80M | 8M | Target bitrate |
| profile | enum | main/main10 | main | H.265 profile |
| gop | int | 1-300 | 30 | Keyframe interval |

### amlge2d (Hardware 2D Engine)

2D graphics engine for scaling, format conversion, and composition.

| Property | Type | Description |
|----------|------|-------------|
| format | enum | Output format (NV12, NV21, RGB) |
| rotation | int | 0, 90, 180, 270 degrees |

**Capabilities:**
- Hardware scaling (resize)
- Color space conversion
- Rotation
- Overlay/blending
- Low CPU usage

**Example - Scale to 720p:**
```
v4l2src device=/dev/vdin1 ! amlge2d ! video/x-raw,width=1280,height=720 ! aml_h264enc ! ...
```

### amlvdec (Hardware Video Decoder)

For transcoding/re-encoding streams.

| Property | Type | Description |
|----------|------|-------------|
| codec | enum | h264/h265/vp9 |

**Example - Transcode:**
```
filesrc location=input.ts ! tsdemux ! h264parse ! amlvdec ! aml_h264enc ! ...
```

### amlvideo2 (Video Capture)

Alternative to v4l2src for VDIN capture.

| Property | Type | Description |
|----------|------|-------------|
| device | string | Device path (/dev/vdin1) |

---

## Audio Subsystem

### ALSA Devices

| Device | Path | Description |
|--------|------|-------------|
| HDMI Audio | hw:0,0 | HDMI-In audio capture |
| I2S Out | hw:1,0 | I2S audio output (if available) |

**Audio Format:**
```bash
# Check supported formats
arecord -D hw:0,0 --dump-hw-params
```

Common format: S16_LE, 48000Hz, Stereo

**GStreamer usage:**
```
alsasrc device=hw:0,0 ! audio/x-raw,rate=48000,channels=2 ! ...
```

---

## Storage

| Path | Type | Mount Point | Notes |
|------|------|-------------|-------|
| Internal | eMMC | /data | ~8GB, fast |
| SD Card | microSD | /mnt/sdcard | User removable |
| USB | USB drive | /mnt/usb | Auto-mounted |

**Check availability:**
```python
import os
os.path.ismount("/mnt/sdcard")
os.statvfs("/mnt/sdcard").f_frsize * os.statvfs("/mnt/sdcard").f_bavail  # Free bytes
```

---

## CEC (Consumer Electronics Control)

For detecting TV power state.

| Interface | Path |
|-----------|------|
| CEC device | /dev/cec0 |
| Sysfs | /sys/class/cec/ |

**Note:** CEC support varies by TV. Not all TVs report power state.

---

## Permissions

### Device Access

| Device | Owner | Group | Permissions |
|--------|-------|-------|-------------|
| /dev/vdin1 | root | video | 660 |
| /dev/video* | root | video | 660 |
| /dev/cec0 | root | video | 660 |

**Service user must be in `video` group, or run as root.**

### File Locations

| Path | Permissions | Owner |
|------|-------------|-------|
| /var/lib/gst-manager/ | 755 | root |
| /var/lib/gst-manager/config.json | 600 | root |
| /usr/lib/gst-manager/ | 755 | root |

---

## Network

### Internet Access

Required for AI API calls. If behind proxy:

```bash
export HTTP_PROXY=http://proxy:port
export HTTPS_PROXY=http://proxy:port
```

Or configure in `/var/lib/gst-manager/config.json`:
```json
{
  "proxy": {
    "http": "http://proxy:port",
    "https": "http://proxy:port"
  }
}
```

### Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 9090 | HTTP/WS | Cockpit web UI |
| 5000+ | SRT | SRT streaming (configurable) |
| 1935 | RTMP | RTMP streaming |
