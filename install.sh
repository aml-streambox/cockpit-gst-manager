#!/bin/bash
# install.sh - Install/reinstall cockpit-gst-manager locally
#
# Usage: ./install.sh
# Run this script on the target device after cloning the repo

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== cockpit-gst-manager Local Installer ==="

# Check if we're in the right directory
if [ ! -d "${SCRIPT_DIR}/backend" ] || [ ! -d "${SCRIPT_DIR}/frontend" ]; then
    echo "Error: Run this script from the cockpit-gst-manager directory"
    exit 1
fi

# Check root
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: This script must be run as root"
    exit 1
fi

echo "[1/6] Stopping existing service..."
systemctl stop gst-manager 2>/dev/null || true

echo "[2/6] Creating directories..."
mkdir -p /usr/share/cockpit/gst-manager
mkdir -p /usr/lib/gst-manager/ai
mkdir -p /usr/share/doc/gst-manager/skills
mkdir -p /var/lib/gst-manager/instances

echo "[3/6] Installing backend..."
cp -f ${SCRIPT_DIR}/backend/*.py /usr/lib/gst-manager/
cp -f ${SCRIPT_DIR}/backend/ai/*.py /usr/lib/gst-manager/ai/
install -m 0755 ${SCRIPT_DIR}/cli/gst-manager-cli /usr/bin/gst-manager-cli
cp -f ${SCRIPT_DIR}/doc/skills/*.md /usr/share/doc/gst-manager/skills/

echo "[4/6] Installing frontend..."
cp -f ${SCRIPT_DIR}/frontend/* /usr/share/cockpit/gst-manager/

echo "[5/6] Installing systemd service..."
cp -f ${SCRIPT_DIR}/yocto/files/gst-manager.service /etc/systemd/system/

echo "[6/6] Configuring D-Bus policy..."
cat > /etc/dbus-1/system.d/org.cockpit.GstManager.conf << 'EOF'
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy user="root">
    <allow own="org.cockpit.GstManager"/>
    <allow send_destination="org.cockpit.GstManager"/>
  </policy>
  <policy context="default">
    <allow send_destination="org.cockpit.GstManager"/>
  </policy>
</busconfig>
EOF

# Install sample config if none exists
if [ ! -f /var/lib/gst-manager/config.json ]; then
    echo "Installing sample config..."
    cp ${SCRIPT_DIR}/samples/config.json /var/lib/gst-manager/config.json
fi

echo "Reloading systemd and starting service..."
systemctl daemon-reload
systemctl enable gst-manager
systemctl restart gst-manager

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Service status:"
systemctl status gst-manager --no-pager -l || true
echo ""
echo "Access Cockpit: https://$(hostname -I | awk '{print $1}'):9090"
echo ""
echo "To configure AI, edit: /var/lib/gst-manager/config.json"
