"""Event Monitoring - HDMI signal detection and event triggers.

Monitors HDMI input signal via tvservice or sysfs and triggers pipeline events.
Prefers tvservice integration when libtvclient.so is available.
"""

import asyncio
import logging
import os
import re
import time
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
    hdr_info: int = 0  # HdrType enum (0=SDR, 1=HDR10, 2=HLG, 3=HDR10+, 4=DV)
    hdr_status: str = ""  # Raw sysfs string (e.g. "HDR10", "HLG", "SDR")
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
            "hdr_info": self.hdr_info,
            "hdr_status": self.hdr_status,
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

    Handles multiple formats:
    - "1920x1080p60hz" format
    - Amlogic hdmirx format with Hactive/Vactive/Frame Rate

    Examples:
        "1920x1080p60hz" -> {width: 1920, height: 1080, fps: 60}
        "Hactive: 3840\nVactive: 2160\nFrame Rate: 5993" -> {width: 3840, height: 2160, fps: 60}

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

    # Pattern 1: WIDTHxHEIGHT[p|i]FPShz (traditional format)
    match = re.search(r"(\d+)x(\d+)([pi])(\d+)", info_str.lower())
    if match:
        result["width"] = int(match.group(1))
        result["height"] = int(match.group(2))
        result["interlaced"] = match.group(3) == "i"
        result["fps"] = int(match.group(4))

    # Pattern 2: Amlogic hdmirx format
    # Hactive: 3840
    # Vactive: 2160
    # Frame Rate: 5993 (599.3Hz / 100 = 5.993Hz, actually 59.93Hz)
    if result["width"] == 0:
        hactive_match = re.search(r"[Hh]active:\s*(\d+)", info_str)
        vactive_match = re.search(r"[Vv]active:\s*(\d+)", info_str)
        fps_match = re.search(r"[Ff]rame [Rr]ate:\s*(\d+)", info_str)
        
        if hactive_match and vactive_match:
            result["width"] = int(hactive_match.group(1))
            result["height"] = int(vactive_match.group(1))
            
            if fps_match:
                fps_val = int(fps_match.group(1))
                # Frame rate might be in format 5993 (meaning 59.93Hz) or 60
                if fps_val > 100:
                    result["fps"] = round(fps_val / 100)
                else:
                    result["fps"] = fps_val
            
            # Check for interlace
            interlace_match = re.search(r"[Ii]nterlace:\s*(\d+)", info_str)
            if interlace_match:
                result["interlaced"] = interlace_match.group(1) == "1"

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

    The polling loop supports *interrupt-style* waking: external code
    (e.g. a native tvserver event callback) can call :meth:`interrupt` to
    force an immediate re-poll without waiting for the full interval.
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
        # Used to wake the poll loop early from a native event
        self._wake_event: Optional[asyncio.Event] = None

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
            status.hdr_status = info.hdr_status

            logger.debug(f"TvService status: {status.resolution}, allm={status.allm_mode}, vrr={status.vrr_mode}, hdr={status.hdr_status}({status.hdr_info})")

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
        else:
            # Some platforms don't have signal file
            # Will determine signal_locked from info file below
            pass

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

                # If we got valid resolution, signal is locked
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
        self._wake_event = asyncio.Event()
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        """Stop the monitoring loop."""
        logger.info("Stopping HDMI monitor")
        self.running = False
        # Wake the loop so it exits promptly
        if self._wake_event:
            self._wake_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def interrupt(self) -> None:
        """Wake the polling loop immediately for an early re-check.

        Thread-safe: can be called from any thread via
        ``loop.call_soon_threadsafe(monitor.interrupt)``.
        """
        if self._wake_event:
            self._wake_event.set()

    async def _monitor_loop(self) -> None:
        """Main monitoring loop with adaptive polling.

        Sleeps are interruptible via ``_wake_event``.  When a native
        tvserver event fires (signal change, cable connect/disconnect),
        the event is set and the loop re-polls immediately.
        """
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

                # Interruptible sleep: returns early if _wake_event is set
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(
                        self._wake_event.wait(), timeout=interval
                    )
                    # Woken by native event — re-poll immediately
                    logger.debug("HDMI poll loop woken by native event")
                except asyncio.TimeoutError:
                    pass

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
            new_status.fps != self.last_status.fps or
            new_status.hdr_info != self.last_status.hdr_info
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
    """Coordinates event monitoring and instance triggers.
    
    Monitors both HDMI RX (input) and TX (output) for passthrough detection.
    When RX is stable, waits 1.5 seconds for TX stabilization, then checks
    TX status via sysfs.
    """

    def __init__(
        self,
        instance_manager,
        service=None,
        auto_instance_manager=None
    ):
        """Initialize event manager.

        Args:
            instance_manager: InstanceManager for triggering actions.
            service: D-Bus service for signal emission.
            auto_instance_manager: AutoInstanceManager for auto instance control.
        """
        self.instance_manager = instance_manager
        self.service = service
        self.auto_instance_manager = auto_instance_manager
        self.hdmi_monitor: Optional[HdmiMonitor] = None
        self.last_hdmi_status: Optional[HdmiStatus] = None
        
        # TX state tracking
        self._rx_stable_time: Optional[float] = None
        self._tx_status: Optional[Any] = None
        self._tx_check_task: Optional[asyncio.Task] = None
        self._last_passthrough_state: Optional[Dict[str, Any]] = None

    async def start(self) -> None:
        """Start all event monitors."""
        # Start HDMI monitor (for RX)
        self.hdmi_monitor = HdmiMonitor(
            on_status_change=self._on_hdmi_status_change,
            on_signal_ready=self._on_hdmi_signal_ready,
            on_signal_lost=self._on_hdmi_signal_lost
        )
        await self.hdmi_monitor.start()
        
        # Wire up native tvserver events → HdmiMonitor interrupt
        # This makes the polling loop wake immediately when tvserver
        # detects a signal change, instead of waiting 2-5 seconds.
        self._setup_native_event_bridge()
        
        # Check initial state - HDMI might already be stable
        # This handles the case where gst-manager starts after HDMI is already settled
        await self._check_initial_state()
    
    def _setup_native_event_bridge(self) -> None:
        """Bridge native tvserver events to the HdmiMonitor poll loop.
        
        If the HdmiMonitor's tvservice client has native events enabled,
        register an additional callback that wakes the HdmiMonitor's
        polling loop immediately on signal/connect events.
        """
        if not TVSERVICE_AVAILABLE or not self.hdmi_monitor:
            return
        
        try:
            # Access the tvservice client that HdmiMonitor lazily creates.
            # Force it to initialize now so we can register the callback.
            if self.hdmi_monitor._tvservice_client is None:
                client = tvservice.TvClientLib()
                if client.connect():
                    self.hdmi_monitor._tvservice_client = client
                else:
                    logger.debug("Could not connect tvservice client for native events")
                    return
            
            tv_client = self.hdmi_monitor._tvservice_client
            if not tv_client or not tv_client._lib:
                return
            
            loop = asyncio.get_running_loop()
            hdmi_monitor = self.hdmi_monitor
            
            def _on_tv_event(event_type: int, event_data) -> None:
                """C callback → wake HdmiMonitor + schedule TX check."""
                try:
                    ev_name = tvservice.TvEventType(event_type).name
                except ValueError:
                    ev_name = str(event_type)
                
                logger.info(f"Native tvserver event → HdmiMonitor: type={ev_name} ({event_type})")
                
                if event_type in (
                    tvservice.TvEventType.TV_EVENT_TYPE_SIGLE_DETECT,
                    tvservice.TvEventType.TV_EVENT_TYPE_SOURCE_CONNECT,
                ):
                    # Wake the HdmiMonitor poll loop (thread-safe)
                    loop.call_soon_threadsafe(hdmi_monitor.interrupt)
            
            tv_client.set_event_callback(_on_tv_event)
            logger.info("Native tvserver event bridge established (tvserver → HdmiMonitor)")
        except Exception as e:
            logger.warning(f"Failed to set up native event bridge: {e}")
    
    async def _check_initial_state(self) -> None:
        """Check if HDMI is already stable on startup."""
        try:
            # Poll for RX status up to 10 seconds (HDMI might take time to stabilize)
            for attempt in range(10):
                await asyncio.sleep(1)
                
                if not self.hdmi_monitor:
                    continue
                    
                rx_status = self.hdmi_monitor.get_status()
                logger.info(f"Initial check attempt {attempt+1}: locked={rx_status.signal_locked}, "
                           f"w={rx_status.width}, h={rx_status.height}, source={rx_status.source}")
                
                # Store the status
                self.last_hdmi_status = rx_status
                
                # If RX is stable (signal_locked and has resolution), we're done
                if rx_status.signal_locked and rx_status.width > 0 and rx_status.height > 0:
                    logger.info("HDMI RX is stable, checking TX status")
                    self._rx_stable_time = time.time()
                    await self._check_tx_status()
                    return
            
            # If we get here, RX never became stable in 10 seconds
            logger.info("HDMI RX did not stabilize in 10 seconds, starting periodic polling")
            asyncio.create_task(self._periodic_polling())
            
        except Exception as e:
            logger.warning(f"Failed to check initial HDMI state: {e}", exc_info=True)
    
    async def _periodic_polling(self) -> None:
        """Poll HDMI state periodically until stable."""
        while self._rx_stable_time is None:
            try:
                await asyncio.sleep(3)  # Check every 3 seconds
                
                if self.hdmi_monitor:
                    rx_status = self.hdmi_monitor.get_status()
                    if rx_status.signal_locked and rx_status.width > 0 and rx_status.height > 0:
                        logger.info(f"HDMI RX became stable: {rx_status.resolution}")
                        self._rx_stable_time = time.time()
                        self.last_hdmi_status = rx_status
                        # Wait 1.5s then check TX
                        await asyncio.sleep(1.5)
                        await self._check_tx_status()
                        break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Periodic polling error: {e}")

    async def stop(self) -> None:
        """Stop all event monitors."""
        if self.hdmi_monitor:
            await self.hdmi_monitor.stop()
        if self._tx_check_task:
            self._tx_check_task.cancel()
            try:
                await self._tx_check_task
            except asyncio.CancelledError:
                pass

    def get_hdmi_status(self) -> Dict[str, Any]:
        """Get current HDMI status."""
        if self.hdmi_monitor:
            status = self.hdmi_monitor.get_status()
            return status.to_dict()
        return {"available": False}
    
    def get_passthrough_state(self) -> Dict[str, Any]:
        """Get current HDMI passthrough state.
        
        vdin1 is a loopback of hdmitx (HDMI TX output).
        Once hdmitx is ready, we can capture from vdin1.
        
        Returns:
            Dictionary with RX/TX status and capture readiness
        """
        rx_stable = self._rx_stable_time is not None
        # TX ready means ready=1 and has valid resolution
        tx_ready = (
            self._tx_status is not None and 
            self._tx_status.ready and 
            self._tx_status.width > 0 and 
            self._tx_status.height > 0
        )
        
        return {
            "rx_connected": self.last_hdmi_status.cable_connected if self.last_hdmi_status else False,
            "rx_stable": rx_stable,
            "rx_signal_locked": self.last_hdmi_status.signal_locked if self.last_hdmi_status else False,
            "tx_connected": self._tx_status.connected if self._tx_status else False,
            "tx_ready": tx_ready,
            "tx_enabled": self._tx_status.enabled if self._tx_status else False,
            "width": self._tx_status.width if self._tx_status else 0,
            "height": self._tx_status.height if self._tx_status else 0,
            "framerate": self._tx_status.fps if self._tx_status else 0,
            "resolution": self._tx_status.resolution if self._tx_status else "",
            # HDR info from RX signal
            "color_depth": self.last_hdmi_status.color_depth if self.last_hdmi_status else 8,
            "hdr_info": self.last_hdmi_status.hdr_info if self.last_hdmi_status else 0,
            "hdr_status": self.last_hdmi_status.hdr_status if self.last_hdmi_status else "",
            "source_is_hdr": (self.last_hdmi_status.hdr_info > 0) if self.last_hdmi_status else False,
            # Can capture when RX is stable AND TX is ready with valid resolution
            "can_capture": rx_stable and tx_ready
        }

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
        """Handle HDMI RX signal becoming stable.
        
        Schedules TX check after 1.5 second delay for TX stabilization.
        """
        logger.info(f"HDMI RX signal ready: {status.resolution}")
        
        # Mark RX as stable
        self._rx_stable_time = time.time()
        logger.info(f"RX marked as stable, scheduling TX check in 1.5s")
        
        # Cancel any pending TX check
        if self._tx_check_task and not self._tx_check_task.done():
            logger.debug("Cancelling previous TX check task")
            self._tx_check_task.cancel()
        
        # Schedule TX check after 1.5 seconds
        self._tx_check_task = asyncio.create_task(
            self._delayed_tx_check()
        )

    async def _delayed_tx_check(self) -> None:
        """Wait 1.5 seconds then check TX status."""
        try:
            await asyncio.sleep(1.5)  # Wait for TX to stabilize
            
            # Check if RX is still stable
            if self._rx_stable_time is None:
                logger.debug("RX no longer stable, skipping TX check")
                return
            
            await self._check_tx_status()
            
        except asyncio.CancelledError:
            logger.debug("TX check cancelled")
        except Exception as e:
            logger.error(f"Error in delayed TX check: {e}")

    async def _check_tx_status(self) -> None:
        """Check HDMI TX status and update passthrough state."""
        try:
            # Get TX status via sysfs
            from tvservice import TvClientLib
            client = TvClientLib()
            self._tx_status = client.get_hdmi_tx_status()
            
            logger.info(f"HDMI TX status: ready={self._tx_status.ready}, "
                       f"connected={self._tx_status.connected}, "
                       f"resolution={self._tx_status.width}x{self._tx_status.height}")
            
            # Evaluate passthrough state
            await self._evaluate_passthrough_state()
            
        except Exception as e:
            logger.error(f"Failed to check TX status: {e}")

    async def _evaluate_passthrough_state(self) -> None:
        """Evaluate and act on passthrough state changes."""
        current_state = self.get_passthrough_state()
        
        # Check if state changed
        state_changed = (
            self._last_passthrough_state is None or
            self._last_passthrough_state.get("can_capture") != current_state.get("can_capture") or
            self._last_passthrough_state.get("resolution") != current_state.get("resolution")
        )
        
        if not state_changed:
            return
        
        self._last_passthrough_state = current_state
        
        logger.info(
            f"Passthrough state: can_capture={current_state['can_capture']}, "
            f"resolution={current_state['resolution']}"
        )
        
        # Emit D-Bus signal
        if self.service:
            try:
                import json
                self.service.emit_passthrough_state(
                    current_state["can_capture"],
                    json.dumps(current_state)
                )
            except Exception as e:
                logger.error(f"Failed to emit passthrough state: {e}")
        
        # Handle state change
        if current_state["can_capture"]:
            # Passthrough ready - notify auto instance manager
            if self.auto_instance_manager:
                try:
                    await self.auto_instance_manager.on_passthrough_ready(self._tx_status)
                except Exception as e:
                    logger.error(f"Auto instance manager error: {e}")
        else:
            # Passthrough lost
            if self.auto_instance_manager:
                try:
                    await self.auto_instance_manager.on_passthrough_lost()
                except Exception as e:
                    logger.error(f"Auto instance manager error: {e}")

    async def _on_hdmi_signal_lost(self) -> None:
        """Handle HDMI signal lost - stop dependent instances."""
        logger.info("HDMI RX signal lost")
        
        # Mark RX as not stable
        self._rx_stable_time = None
        self._tx_status = None
        
        # Cancel any pending TX check
        if self._tx_check_task and not self._tx_check_task.done():
            self._tx_check_task.cancel()
        
        # Evaluate new state (will trigger passthrough lost)
        await self._evaluate_passthrough_state()
        
        # Also handle legacy HDMI signal ready instances
        for instance in list(self.instance_manager.instances.values()):
            if (instance.status.value == "running" and
                    "/dev/vdin1" in instance.pipeline):
                logger.info(f"Stopping HDMI-dependent instance: {instance.id}")
                try:
                    await self.instance_manager.stop_instance(instance.id)
                except Exception as e:
                    logger.error(f"Failed to stop {instance.id}: {e}")
