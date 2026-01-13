"""Event Monitoring - HDMI signal detection and event triggers.

Monitors HDMI input signal via tvservice or sysfs and triggers pipeline events.
Prefers tvservice integration when libtvclient.so is available.
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable, Any, Dict

logger = logging.getLogger("gst-manager.events")

# Try to import tvservice module for native integration
try:
    from . import tvservice
    TVSERVICE_AVAILABLE = True
    logger.info("TvService module available")
except ImportError:
    TVSERVICE_AVAILABLE = False
    logger.debug("TvService module not available, using fallback")


# Sysfs paths for HDMI RX
HDMIRX_SYSFS_PATHS = [
    "/sys/class/hdmirx/hdmirx0",
    "/sys/class/hdmirx/hdmirx1",
    "/sys/kernel/debug/hdmirx",
    "/sys/devices/platform/hdmirx",
]


@dataclass
class HdmiStatus:
    """HDMI input signal status."""
    available: bool = False
    cable_connected: bool = False
    signal_locked: bool = False
    width: int = 0
    height: int = 0
    fps: int = 0
    interlaced: bool = False
    color_format: str = ""
    color_depth: int = 8
    raw_info: str = ""
    # Extended info from tvservice
    allm_mode: int = 0
    vrr_mode: int = 0
    hdr_info: int = 0
    source: str = "unknown"  # polling, tvservice, sysfs, v4l2

    @property
    def resolution(self) -> str:
        """Get resolution string like '1920x1080p60'."""
        if not self.signal_locked:
            return ""
        i = "i" if self.interlaced else "p"
        return f"{self.width}x{self.height}{i}{self.fps}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "available": self.available,
            "cable_connected": self.cable_connected,
            "signal_locked": self.signal_locked,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "interlaced": self.interlaced,
            "resolution": self.resolution,
            "color_format": self.color_format,
            "color_depth": self.color_depth,
            "allm_mode": self.allm_mode,
            "vrr_mode": self.vrr_mode,
            "source": self.source
        }


def find_hdmirx_sysfs() -> Optional[Path]:
    """Find available HDMI RX sysfs path."""
    for path in HDMIRX_SYSFS_PATHS:
        p = Path(path)
        if p.exists():
            logger.debug(f"Found HDMI RX sysfs at: {path}")
            return p
    return None


def parse_hdmi_info(info_str: str) -> Dict[str, Any]:
    """Parse HDMI info string to extract resolution and framerate.

    Examples:
        "1920x1080p60hz" -> {width: 1920, height: 1080, fps: 60, interlaced: False}
        "3840x2160p30hz" -> {width: 3840, height: 2160, fps: 30, interlaced: False}
        "1920x1080i50hz" -> {width: 1920, height: 1080, fps: 50, interlaced: True}

    Args:
        info_str: Raw info string from sysfs.

    Returns:
        Dict with parsed values.
    """
    result = {
        "width": 0,
        "height": 0,
        "fps": 0,
        "interlaced": False,
        "color_format": ""
    }

    if not info_str:
        return result

    # Pattern: WIDTHxHEIGHT[p|i]FPShz
    match = re.search(r"(\d+)x(\d+)([pi])(\d+)", info_str.lower())
    if match:
        result["width"] = int(match.group(1))
        result["height"] = int(match.group(2))
        result["interlaced"] = match.group(3) == "i"
        result["fps"] = int(match.group(4))

    # Try to extract color format
    color_match = re.search(r"(rgb|yuv|ycbcr)\d*", info_str.lower())
    if color_match:
        result["color_format"] = color_match.group(0).upper()

    return result


def read_sysfs_file(path: Path) -> str:
    """Read a sysfs file safely."""
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except (IOError, OSError) as e:
        logger.debug(f"Failed to read {path}: {e}")
        return ""


class HdmiMonitor:
    """Monitors HDMI input signal via sysfs polling.

    Uses adaptive polling intervals:
    - 2 seconds when no signal
    - 5 seconds when signal is active
    - 500ms stability check after change
    """

    POLL_NO_SIGNAL = 2.0
    POLL_SIGNAL_ACTIVE = 5.0
    POLL_STABILITY_CHECK = 0.5

    def __init__(
        self,
        on_status_change: Optional[Callable[[HdmiStatus], Any]] = None,
        on_signal_ready: Optional[Callable[[HdmiStatus], Any]] = None,
        on_signal_lost: Optional[Callable[[], Any]] = None
    ):
        """Initialize HDMI monitor.

        Args:
            on_status_change: Callback for any status change.
            on_signal_ready: Callback when signal becomes available.
            on_signal_lost: Callback when signal is lost.
        """
        self.on_status_change = on_status_change
        self.on_signal_ready = on_signal_ready
        self.on_signal_lost = on_signal_lost

        self.sysfs_path: Optional[Path] = None
        self.running = False
        self.last_status: Optional[HdmiStatus] = None
        self._task: Optional[asyncio.Task] = None
        self._tvservice_client: Optional[Any] = None

    def get_status(self) -> HdmiStatus:
        """Read current HDMI status.

        Tries sources in order:
        1. TvService (native library) - best quality, instant events
        2. Sysfs paths - direct kernel interface
        3. V4L2-ctl command - fallback subprocess call

        Returns:
            HdmiStatus with current state.
        """
        # Try tvservice first (best source)
        if TVSERVICE_AVAILABLE:
            status = self._get_status_tvservice()
            if status.available:
                return status

        # Try sysfs paths
        if self.sysfs_path is None:
            self.sysfs_path = find_hdmirx_sysfs()

        if self.sysfs_path is not None:
            status = self._get_status_sysfs()
            if status.available:
                return status

        # Fallback to v4l2-ctl
        return self._get_status_v4l2()

    def _get_status_tvservice(self) -> HdmiStatus:
        """Get status from tvservice library."""
        status = HdmiStatus(source="tvservice")

        try:
            # Lazy init tvservice client
            if self._tvservice_client is None:
                self._tvservice_client = tvservice.TvClientLib()
                if not self._tvservice_client.connect():
                    self._tvservice_client = None
                    return status

            if not self._tvservice_client.available:
                return status

            # Get signal info from tvservice
            info = self._tvservice_client.get_signal_info(tvservice.TvSourceInput.SOURCE_HDMI1)
            
            status.available = True
            status.width = info.width
            status.height = info.height
            status.fps = info.fps
            status.color_depth = info.color_depth
            status.signal_locked = info.is_stable
            status.cable_connected = status.signal_locked or info.status != tvservice.TvinSigStatus.TVIN_SIG_STATUS_NOSIG
            status.allm_mode = info.allm_mode
            status.vrr_mode = info.vrr_mode
            status.hdr_info = info.hdr_info

            logger.debug(f"TvService status: {status.resolution}, allm={status.allm_mode}, vrr={status.vrr_mode}")

        except Exception as e:
            logger.warning(f"TvService get_status failed: {e}")
            status.available = False

        return status

    def _get_status_sysfs(self) -> HdmiStatus:
        """Get status from sysfs paths."""
        status = HdmiStatus(source="sysfs")
        status.available = True

        # Read cable status
        cable_path = self.sysfs_path / "cable"
        if cable_path.exists():
            cable = read_sysfs_file(cable_path)
            status.cable_connected = cable in ("1", "connected", "true")
        else:
            # Assume cable connected if we can read other files
            status.cable_connected = True

        # Read signal lock status
        signal_path = self.sysfs_path / "signal"
        if signal_path.exists():
            signal = read_sysfs_file(signal_path)
            status.signal_locked = signal in ("1", "locked", "true")

        # Read info (resolution/format)
        info_path = self.sysfs_path / "info"
        if info_path.exists():
            info = read_sysfs_file(info_path)
            status.raw_info = info
            if info:
                parsed = parse_hdmi_info(info)
                status.width = parsed["width"]
                status.height = parsed["height"]
                status.fps = parsed["fps"]
                status.interlaced = parsed["interlaced"]
                status.color_format = parsed["color_format"]

                # If we got valid resolution, assume signal is locked
                if status.width > 0 and status.height > 0:
                    status.signal_locked = True

        return status

    def _get_status_v4l2(self) -> HdmiStatus:
        """Fallback: Get status via v4l2-ctl command on /dev/video71."""
        status = HdmiStatus(source="v4l2")

        # Check HDMI cable connection via /dev/hdmirx0
        if Path("/dev/hdmirx0").exists():
            try:
                # Read 5V/HPD status
                with open("/dev/hdmirx0", "rb") as f:
                    import struct
                    hdmi_status = struct.unpack("i", f.read(4))[0]
                    status.cable_connected = (hdmi_status & 0x01) != 0  # Port A
                    status.available = True
            except Exception as e:
                logger.debug(f"Failed to read /dev/hdmirx0: {e}")

        # Try different V4L2 devices for signal info
        v4l2_devices = ["/dev/video71", "/dev/video0", "/dev/vdin0"]
        
        for device in v4l2_devices:
            if not Path(device).exists():
                continue
                
            status.available = True
            
            try:
                import subprocess
                result = subprocess.run(
                    ["v4l2-ctl", "-d", device, "--query-dv-timings"],
                    capture_output=True,
                    text=True,
                    timeout=2
                )

                if result.returncode == 0 and result.stdout:
                    status.cable_connected = True
                    status.signal_locked = True
                    status.raw_info = result.stdout

                    # Parse width/height from v4l2-ctl output
                    # Format: "Active width: 1920" or "Width: 1920"
                    width_match = re.search(r"(?:Active\s+)?[Ww]idth:\s*(\d+)", result.stdout)
                    height_match = re.search(r"(?:Active\s+)?[Hh]eight:\s*(\d+)", result.stdout)
                    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", result.stdout)
                    # Also try: "Pixelclock: 148500000 Hz (1920x1080p59.94)"
                    res_match = re.search(r"\((\d+)x(\d+)([pi])(\d+(?:\.\d+)?)\)", result.stdout)

                    if width_match:
                        status.width = int(width_match.group(1))
                    if height_match:
                        status.height = int(height_match.group(1))
                    if fps_match:
                        status.fps = int(float(fps_match.group(1)))
                    
                    # Use the parenthetical format if direct matches failed
                    if res_match and (status.width == 0 or status.height == 0):
                        status.width = int(res_match.group(1))
                        status.height = int(res_match.group(2))
                        status.interlaced = res_match.group(3) == 'i'
                        status.fps = int(float(res_match.group(4)))

                    if status.width > 0:
                        logger.debug(f"v4l2-ctl on {device}: {status.width}x{status.height}@{status.fps}")
                        break  # Found working device
                        
            except subprocess.TimeoutExpired:
                logger.debug(f"v4l2-ctl timeout on {device}")
            except FileNotFoundError:
                logger.debug("v4l2-ctl not found")
                break
            except Exception as e:
                logger.debug(f"v4l2-ctl failed on {device}: {e}")

        return status

    async def start(self) -> None:
        """Start the monitoring loop."""
        if self.running:
            logger.warning("HDMI monitor already running")
            return

        logger.info("Starting HDMI monitor")
        self.running = True
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        """Stop the monitoring loop."""
        logger.info("Stopping HDMI monitor")
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _monitor_loop(self) -> None:
        """Main monitoring loop with adaptive polling."""
        while self.running:
            try:
                status = self.get_status()

                # Check for status change
                if self._status_changed(status):
                    # Wait for stability
                    await asyncio.sleep(self.POLL_STABILITY_CHECK)
                    status = self.get_status()

                    # Still changed after stability check?
                    if self._status_changed(status):
                        await self._handle_status_change(status)
                        self.last_status = status

                # Adaptive polling interval
                interval = (
                    self.POLL_SIGNAL_ACTIVE
                    if status.signal_locked
                    else self.POLL_NO_SIGNAL
                )
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"HDMI monitor error: {e}")
                await asyncio.sleep(self.POLL_NO_SIGNAL)

    def _status_changed(self, new_status: HdmiStatus) -> bool:
        """Check if status has changed significantly."""
        if self.last_status is None:
            return True

        # Check key fields
        return (
            new_status.signal_locked != self.last_status.signal_locked or
            new_status.cable_connected != self.last_status.cable_connected or
            new_status.width != self.last_status.width or
            new_status.height != self.last_status.height or
            new_status.fps != self.last_status.fps
        )

    async def _handle_status_change(self, status: HdmiStatus) -> None:
        """Handle a status change event."""
        was_locked = self.last_status.signal_locked if self.last_status else False
        now_locked = status.signal_locked

        logger.info(
            f"HDMI status changed: locked={now_locked}, "
            f"resolution={status.resolution}"
        )

        # General status change callback
        if self.on_status_change:
            try:
                result = self.on_status_change(status)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Status change callback error: {e}")

        # Signal became available
        if now_locked and not was_locked:
            if self.on_signal_ready:
                try:
                    result = self.on_signal_ready(status)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error(f"Signal ready callback error: {e}")

        # Signal was lost
        elif was_locked and not now_locked:
            if self.on_signal_lost:
                try:
                    result = self.on_signal_lost()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error(f"Signal lost callback error: {e}")


class EventManager:
    """Coordinates event monitoring and instance triggers."""

    def __init__(
        self,
        instance_manager,
        service=None
    ):
        """Initialize event manager.

        Args:
            instance_manager: InstanceManager for triggering actions.
            service: D-Bus service for signal emission.
        """
        self.instance_manager = instance_manager
        self.service = service
        self.hdmi_monitor: Optional[HdmiMonitor] = None
        self.last_hdmi_status: Optional[HdmiStatus] = None

    async def start(self) -> None:
        """Start all event monitors."""
        # Start HDMI monitor
        self.hdmi_monitor = HdmiMonitor(
            on_status_change=self._on_hdmi_status_change,
            on_signal_ready=self._on_hdmi_signal_ready,
            on_signal_lost=self._on_hdmi_signal_lost
        )
        await self.hdmi_monitor.start()

    async def stop(self) -> None:
        """Stop all event monitors."""
        if self.hdmi_monitor:
            await self.hdmi_monitor.stop()

    def get_hdmi_status(self) -> Dict[str, Any]:
        """Get current HDMI status."""
        if self.hdmi_monitor:
            status = self.hdmi_monitor.get_status()
            return status.to_dict()
        return {"available": False}

    async def _on_hdmi_status_change(self, status: HdmiStatus) -> None:
        """Handle HDMI status change - emit D-Bus signal."""
        self.last_hdmi_status = status

        # Emit D-Bus signal
        if self.service:
            try:
                self.service.emit_hdmi_signal(
                    status.signal_locked,
                    status.resolution
                )
            except Exception as e:
                logger.error(f"Failed to emit HDMI signal: {e}")

    async def _on_hdmi_signal_ready(self, status: HdmiStatus) -> None:
        """Handle HDMI signal becoming available - auto-start instances."""
        logger.info(f"HDMI signal ready: {status.resolution}")

        # Find instances configured for HDMI auto-start
        for instance in self.instance_manager.instances.values():
            if (instance.trigger_event == "hdmi_signal_ready" and
                    instance.autostart and
                    instance.status.value == "stopped"):
                logger.info(f"Auto-starting instance: {instance.id}")
                try:
                    await self.instance_manager.start_instance(instance.id)
                except Exception as e:
                    logger.error(f"Failed to auto-start {instance.id}: {e}")

    async def _on_hdmi_signal_lost(self) -> None:
        """Handle HDMI signal lost - stop HDMI-dependent instances."""
        logger.info("HDMI signal lost")

        # Find running instances that use HDMI input
        for instance in list(self.instance_manager.instances.values()):
            if (instance.status.value == "running" and
                    "/dev/vdin1" in instance.pipeline):
                logger.info(f"Stopping HDMI-dependent instance: {instance.id}")
                try:
                    await self.instance_manager.stop_instance(instance.id)
                except Exception as e:
                    logger.error(f"Failed to stop {instance.id}: {e}")
