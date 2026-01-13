"""TVService Client - Integration with aml_tvserver_streambox.

Provides access to HDMI signal status and events from the tvserver binder service.
Uses ctypes to call the TvClientWrapper C library.
"""

import asyncio
import ctypes
import logging
import os
from ctypes import c_int, c_char_p, c_void_p, c_size_t, POINTER, Structure, CFUNCTYPE
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Optional, Callable, Any, Dict, List

logger = logging.getLogger("gst-manager.tvservice")


# ============================================================================
# Enums from TvCommon.h / tvcmd.h
# ============================================================================

class TvSourceInput(IntEnum):
    """tv_source_input_t enum."""
    SOURCE_INVALID = -1
    SOURCE_TV = 0
    SOURCE_AV1 = 1
    SOURCE_AV2 = 2
    SOURCE_YPBPR1 = 3
    SOURCE_YPBPR2 = 4
    SOURCE_HDMI1 = 5
    SOURCE_HDMI2 = 6
    SOURCE_HDMI3 = 7
    SOURCE_HDMI4 = 8
    SOURCE_VGA = 9
    SOURCE_MPEG = 10
    SOURCE_MAX = 11


class TvinSigStatus(IntEnum):
    """tvin_sig_status_t enum."""
    TVIN_SIG_STATUS_NULL = 0
    TVIN_SIG_STATUS_NOSIG = 1
    TVIN_SIG_STATUS_UNSTABLE = 2
    TVIN_SIG_STATUS_NOTSUP = 3
    TVIN_SIG_STATUS_STABLE = 4


class TvEventType(IntEnum):
    """event_type_t enum."""
    TV_EVENT_TYPE_COMMON = 0
    TV_EVENT_TYPE_SIGLE_DETECT = 4
    TV_EVENT_TYPE_SOURCE_CONNECT = 10
    TV_EVENT_TYPE_SIG_DV_ALLM = 26


class TvinColorFmt(IntEnum):
    """tvin_color_fmt_t - simplified."""
    TVIN_COLOR_FMT_RGB = 0
    TVIN_COLOR_FMT_YUV422 = 1
    TVIN_COLOR_FMT_YUV444 = 2
    TVIN_COLOR_FMT_YUV420 = 3
    TVIN_COLOR_FMT_MAX = 4


# ============================================================================
# Data structures
# ============================================================================

@dataclass
class SignalInfo:
    """HDMI signal information from tvservice."""
    source: int = TvSourceInput.SOURCE_INVALID
    width: int = 0
    height: int = 0
    fps: int = 0
    color_depth: int = 8
    color_format: int = 0
    status: int = TvinSigStatus.TVIN_SIG_STATUS_NULL
    is_dvi: bool = False
    hdr_info: int = 0
    allm_mode: int = 0
    vrr_mode: int = 0

    @property
    def is_stable(self) -> bool:
        return self.status == TvinSigStatus.TVIN_SIG_STATUS_STABLE

    @property
    def resolution(self) -> str:
        if not self.is_stable or self.width == 0:
            return ""
        return f"{self.width}x{self.height}p{self.fps}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "color_depth": self.color_depth,
            "color_format": self.color_format,
            "status": self.status,
            "status_name": TvinSigStatus(self.status).name if self.status in [s.value for s in TvinSigStatus] else "UNKNOWN",
            "is_stable": self.is_stable,
            "resolution": self.resolution,
            "is_dvi": self.is_dvi,
            "hdr_info": self.hdr_info,
            "allm_mode": self.allm_mode,
            "vrr_mode": self.vrr_mode,
        }


@dataclass 
class SourceConnectInfo:
    """Source connection status."""
    source: int = TvSourceInput.SOURCE_INVALID
    connected: bool = False


# ============================================================================
# C Callback types
# ============================================================================

# typedef void (*EventCallback)(event_type_t eventType, void *eventData);
EventCallbackFunc = CFUNCTYPE(None, c_int, c_void_p)


# ============================================================================
# TvClientWrapper ctypes interface
# ============================================================================

class TvClientLib:
    """Wrapper for libtvclient.so using ctypes."""

    # Library search paths
    LIB_PATHS = [
        "/usr/lib/libtvclient.so",
        "/usr/lib64/libtvclient.so",
        "/lib/libtvclient.so",
        "/system/lib64/libtvclient.so",
        "/vendor/lib64/libtvclient.so",
    ]

    def __init__(self):
        self._lib: Optional[ctypes.CDLL] = None
        self._handle: Optional[c_void_p] = None
        self._callback_ref = None  # prevent GC
        self._event_handlers: List[Callable] = []
        self._load_library()

    def _load_library(self) -> bool:
        """Attempt to load the tvclient shared library."""
        for path in self.LIB_PATHS:
            if os.path.exists(path):
                try:
                    self._lib = ctypes.CDLL(path)
                    logger.info(f"Loaded tvclient library from: {path}")
                    self._setup_functions()
                    return True
                except OSError as e:
                    logger.warning(f"Failed to load {path}: {e}")
        
        logger.warning("TvClient library not found, tvservice integration disabled")
        return False

    def _setup_functions(self):
        """Set up ctypes function prototypes."""
        if not self._lib:
            return

        # struct TvClientWrapper_t *GetInstance(void)
        self._lib.GetInstance.restype = c_void_p
        self._lib.GetInstance.argtypes = []

        # void ReleaseInstance(struct TvClientWrapper_t **ppInstance)
        self._lib.ReleaseInstance.restype = None
        self._lib.ReleaseInstance.argtypes = [POINTER(c_void_p)]

        # int StartTv(struct TvClientWrapper_t *pTvClientWrapper, tv_source_input_t source)
        self._lib.StartTv.restype = c_int
        self._lib.StartTv.argtypes = [c_void_p, c_int]

        # int StopTv(struct TvClientWrapper_t *pTvClientWrapper, tv_source_input_t source)
        self._lib.StopTv.restype = c_int
        self._lib.StopTv.argtypes = [c_void_p, c_int]

        # int GetCurrentSourceFrameHeight(struct TvClientWrapper_t *pTvClientWrapper)
        self._lib.GetCurrentSourceFrameHeight.restype = c_int
        self._lib.GetCurrentSourceFrameHeight.argtypes = [c_void_p]

        # int GetCurrentSourceFrameWidth(struct TvClientWrapper_t *pTvClientWrapper)
        self._lib.GetCurrentSourceFrameWidth.restype = c_int
        self._lib.GetCurrentSourceFrameWidth.argtypes = [c_void_p]

        # int GetCurrentSourceFrameFps(struct TvClientWrapper_t *pTvClientWrapper)
        self._lib.GetCurrentSourceFrameFps.restype = c_int
        self._lib.GetCurrentSourceFrameFps.argtypes = [c_void_p]

        # int GetCurrentSourceColorDepth(struct TvClientWrapper_t *pTvClientWrapper)
        self._lib.GetCurrentSourceColorDepth.restype = c_int
        self._lib.GetCurrentSourceColorDepth.argtypes = [c_void_p]

        # int GetSourceConnectStatus(struct TvClientWrapper_t *pTvClientWrapper, tv_source_input_t source)
        self._lib.GetSourceConnectStatus.restype = c_int
        self._lib.GetSourceConnectStatus.argtypes = [c_void_p, c_int]

        # int setTvEventCallback(EventCallback Callback)
        self._lib.setTvEventCallback.restype = c_int
        self._lib.setTvEventCallback.argtypes = [EventCallbackFunc]

        # VRR/ALLM functions (STREAM_BOX only)
        try:
            self._lib.GetHdmiAllmEnabled.restype = c_int
            self._lib.GetHdmiAllmEnabled.argtypes = [c_void_p]
            
            self._lib.GetHdmiVrrEnabled.restype = c_int
            self._lib.GetHdmiVrrEnabled.argtypes = [c_void_p]
        except AttributeError:
            logger.debug("ALLM/VRR functions not available")

    @property
    def available(self) -> bool:
        """Check if library is loaded and connected."""
        return self._lib is not None and self._handle is not None

    def connect(self) -> bool:
        """Connect to tvservice."""
        if not self._lib:
            return False
        
        try:
            self._handle = self._lib.GetInstance()
            if self._handle:
                logger.info("Connected to tvservice")
                return True
            else:
                logger.error("Failed to get tvservice instance")
                return False
        except Exception as e:
            logger.error(f"Failed to connect to tvservice: {e}")
            return False

    def disconnect(self):
        """Disconnect from tvservice."""
        if self._lib and self._handle:
            try:
                handle_ptr = c_void_p(self._handle)
                self._lib.ReleaseInstance(ctypes.byref(handle_ptr))
                self._handle = None
                logger.info("Disconnected from tvservice")
            except Exception as e:
                logger.error(f"Error disconnecting from tvservice: {e}")

    def get_signal_info(self, source: int = TvSourceInput.SOURCE_HDMI1) -> SignalInfo:
        """Get current signal information."""
        info = SignalInfo(source=source)
        
        if not self.available:
            return info

        try:
            info.width = self._lib.GetCurrentSourceFrameWidth(self._handle)
            info.height = self._lib.GetCurrentSourceFrameHeight(self._handle)
            info.fps = self._lib.GetCurrentSourceFrameFps(self._handle)
            info.color_depth = self._lib.GetCurrentSourceColorDepth(self._handle)
            
            # Determine status based on dimensions
            if info.width > 0 and info.height > 0:
                info.status = TvinSigStatus.TVIN_SIG_STATUS_STABLE
            else:
                connected = self._lib.GetSourceConnectStatus(self._handle, source)
                if connected == 1:
                    info.status = TvinSigStatus.TVIN_SIG_STATUS_UNSTABLE
                else:
                    info.status = TvinSigStatus.TVIN_SIG_STATUS_NOSIG

            # VRR/ALLM info if available
            try:
                info.allm_mode = self._lib.GetHdmiAllmEnabled(self._handle)
                info.vrr_mode = self._lib.GetHdmiVrrEnabled(self._handle)
            except (AttributeError, OSError):
                pass

        except Exception as e:
            logger.error(f"Failed to get signal info: {e}")

        return info

    def get_source_connected(self, source: int) -> bool:
        """Check if a source is connected."""
        if not self.available:
            return False
        try:
            return self._lib.GetSourceConnectStatus(self._handle, source) == 1
        except Exception:
            return False

    def set_event_callback(self, callback: Callable[[int, Any], None]):
        """Set callback for tvservice events."""
        if not self._lib:
            return

        def c_callback(event_type: int, event_data: c_void_p):
            try:
                callback(event_type, event_data)
            except Exception as e:
                logger.error(f"Event callback error: {e}")

        self._callback_ref = EventCallbackFunc(c_callback)
        self._event_handlers.append(callback)
        self._lib.setTvEventCallback(self._callback_ref)


# ============================================================================
# Async TvService Monitor
# ============================================================================

class TvServiceMonitor:
    """Async monitor for tvservice events with fallback to polling."""

    def __init__(
        self,
        source: int = TvSourceInput.SOURCE_HDMI1,
        on_signal_change: Optional[Callable[[SignalInfo], Any]] = None,
        on_source_connect: Optional[Callable[[SourceConnectInfo], Any]] = None,
    ):
        self.source = source
        self.on_signal_change = on_signal_change
        self.on_source_connect = on_source_connect
        
        self._client: Optional[TvClientLib] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_info: Optional[SignalInfo] = None

    @property
    def available(self) -> bool:
        """Check if tvservice is available."""
        return self._client is not None and self._client.available

    async def start(self) -> bool:
        """Start the monitor."""
        if self._running:
            return True

        # Try to connect to tvservice
        self._client = TvClientLib()
        if self._client.connect():
            logger.info("TvService monitor using native library")
        else:
            logger.warning("TvService not available, using polling fallback")
            self._client = None

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        return True

    async def stop(self):
        """Stop the monitor."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._client:
            self._client.disconnect()
            self._client = None

    def get_signal_info(self) -> SignalInfo:
        """Get current signal information."""
        if self._client and self._client.available:
            return self._client.get_signal_info(self.source)
        return SignalInfo(source=self.source)

    async def _monitor_loop(self):
        """Main monitoring loop - polls if native events aren't available."""
        poll_interval = 2.0  # seconds
        
        while self._running:
            try:
                info = self.get_signal_info()
                
                # Check for changes
                if self._signal_changed(info):
                    logger.debug(f"Signal changed: {info.resolution}")
                    if self.on_signal_change:
                        try:
                            result = self.on_signal_change(info)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            logger.error(f"Signal change callback error: {e}")
                    self._last_info = info

                await asyncio.sleep(poll_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
                await asyncio.sleep(poll_interval)

    def _signal_changed(self, new_info: SignalInfo) -> bool:
        """Check if signal has changed significantly."""
        if self._last_info is None:
            return True
        
        return (
            new_info.status != self._last_info.status or
            new_info.width != self._last_info.width or
            new_info.height != self._last_info.height or
            new_info.fps != self._last_info.fps
        )


# ============================================================================
# Singleton instance
# ============================================================================

_monitor_instance: Optional[TvServiceMonitor] = None


def get_tvservice_monitor() -> TvServiceMonitor:
    """Get or create the global TvServiceMonitor instance."""
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = TvServiceMonitor()
    return _monitor_instance
