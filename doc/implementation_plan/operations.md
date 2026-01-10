# Operations and Debugging

## Log Access

### View Daemon Logs

```bash
# Live logs
journalctl -u gst-manager -f

# Last 100 lines
journalctl -u gst-manager -n 100

# Logs since boot
journalctl -u gst-manager -b
```

### GStreamer Debug

Set debug level in service file or environment:

```bash
# Levels: 0=none, 1=error, 2=warning, 3=info, 4=debug, 5=trace
export GST_DEBUG=2

# Specific elements
export GST_DEBUG=aml_h264enc:4,srtsink:3

# To file
export GST_DEBUG_FILE=/tmp/gst-debug.log
```

### Log Locations

| Log | Location |
|-----|----------|
| Daemon logs | journald (`journalctl -u gst-manager`) |
| GStreamer debug | journald or GST_DEBUG_FILE |
| Cockpit logs | journald (`journalctl -u cockpit`) |
| Instance errors | `/var/lib/gst-manager/instances/{id}/error.log` |

---

## Diagnostics Collection

### Quick Health Check

```bash
#!/bin/bash
echo "=== Service Status ==="
systemctl status gst-manager

echo "=== HDMI Signal ==="
cat /sys/class/hdmirx/hdmirx0/info 2>/dev/null || echo "No HDMI sysfs"

echo "=== Video Devices ==="
ls -la /dev/vdin* /dev/video* 2>/dev/null

echo "=== Storage ==="
df -h /mnt/sdcard /data 2>/dev/null

echo "=== GStreamer Plugins ==="
gst-inspect-1.0 aml_h264enc >/dev/null && echo "aml_h264enc: OK" || echo "aml_h264enc: MISSING"
gst-inspect-1.0 srtsink >/dev/null && echo "srtsink: OK" || echo "srtsink: MISSING"

echo "=== Config ==="
cat /var/lib/gst-manager/config.json 2>/dev/null | head -5

echo "=== Running Instances ==="
ps aux | grep gst-launch
```

### Full Diagnostic Bundle

```bash
#!/bin/bash
DIAG_DIR="/tmp/gst-manager-diag-$(date +%Y%m%d-%H%M%S)"
mkdir -p $DIAG_DIR

# System info
uname -a > $DIAG_DIR/system.txt
cat /proc/meminfo > $DIAG_DIR/meminfo.txt

# Service logs
journalctl -u gst-manager --no-pager -n 500 > $DIAG_DIR/gst-manager.log

# GStreamer info
gst-inspect-1.0 --version > $DIAG_DIR/gst-version.txt
gst-inspect-1.0 aml_h264enc > $DIAG_DIR/aml_h264enc.txt 2>&1
gst-inspect-1.0 aml_h265enc > $DIAG_DIR/aml_h265enc.txt 2>&1

# Config (redact API keys)
cat /var/lib/gst-manager/config.json | sed 's/"api_key": "[^"]*"/"api_key": "REDACTED"/g' > $DIAG_DIR/config.json

# HDMI status
cat /sys/class/hdmirx/hdmirx0/* > $DIAG_DIR/hdmi.txt 2>&1

# Create tarball
tar czf ${DIAG_DIR}.tar.gz -C /tmp $(basename $DIAG_DIR)
echo "Diagnostic bundle: ${DIAG_DIR}.tar.gz"
```

---

## Common Issues

### Service Not Starting

```bash
# Check status
systemctl status gst-manager

# Check logs
journalctl -u gst-manager -n 50

# Common causes:
# - Python dependency missing
# - D-Bus configuration issue
# - Permission denied on files
```

### Pipeline Fails to Start

```bash
# Test pipeline manually
gst-launch-1.0 -v <pipeline command>

# Check device availability
v4l2-ctl -d /dev/vdin1 --all

# Check encoder
gst-inspect-1.0 aml_h264enc
```

### No HDMI Signal Detected

```bash
# Check sysfs
cat /sys/class/hdmirx/hdmirx0/signal
cat /sys/class/hdmirx/hdmirx0/info

# Check if driver loaded
lsmod | grep hdmirx
```

### AI Not Responding

```bash
# Check network
ping -c 1 open.bigmodel.cn

# Check API config
cat /var/lib/gst-manager/config.json | jq '.ai_providers'

# Test API manually
curl -X POST "API_URL" \
  -H "Authorization: Bearer API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"MODEL","messages":[{"role":"user","content":"test"}]}'
```

---

## Performance Monitoring

### Memory Usage

```bash
# Daemon memory
ps -o pid,vsz,rss,comm -p $(pidof python3)

# All GStreamer processes
ps aux | grep gst-launch | awk '{sum+=$6} END {print "Total RSS: " sum/1024 " MB"}'
```

### CPU Usage

```bash
top -p $(pidof python3)
```

---

## Restart and Recovery

```bash
# Restart daemon
systemctl restart gst-manager

# Force stop all pipelines
pkill -f gst-launch-1.0

# Reset config (caution: loses settings)
rm /var/lib/gst-manager/config.json
systemctl restart gst-manager
```
