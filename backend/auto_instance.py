"""Auto Instance Manager - Single auto-managed GStreamer instance.

Manages a single auto-generated instance that:
1. Captures HDMI via streamboxsrc (Path A/B) or legacy v4l2src
2. Uses dynamic resolution/framerate from HDMI TX
3. Auto-starts/stops based on HDMI RX/TX state
4. Supports SRT streaming (always on) + optional recording (MPEG-TS)

Capture sources:
- vfmcap (Path A): streamboxsrc source=vfmcap — raw/low-latency (default)
- vdin1 (Path B): streamboxsrc source=vdin1 — color-processed via VPP
- v4l2_legacy: v4l2src device=/dev/video71 — deprecated, backward compat
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger("gst-manager.auto_instance")


class CaptureSource(Enum):
    """Video capture source options.
    
    vfmcap:     Path A — streamboxsrc source=vfmcap (raw/low-latency, default)
    vdin1:      Path B — streamboxsrc source=vdin1 (color-processed via VPP)
    v4l2_legacy: Legacy — v4l2src device=/dev/video71 (deprecated vdin1 capture)
    """
    VFMCAP = "vfmcap"          # Path A: raw vfm_cap capture
    VDIN1 = "vdin1"            # Path B: vdin1 with VPP color processing
    V4L2_LEGACY = "v4l2_legacy"  # Legacy: v4l2src /dev/video71 (deprecated)


class AudioSource(Enum):
    """Audio input source options."""
    HDMI_RX = "hdmi_rx"  # hw:0,6 - HDMI RX loopback audio
    LINE_IN = "line_in"  # hw:0,0 - Line in audio


@dataclass
class AutoInstanceConfig:
    """Configuration for auto-generated instance.
    
    GOP is calculated as: framerate * gop_interval_seconds
    """
    # Capture source selection
    capture_source: CaptureSource = CaptureSource.VFMCAP  # Default: Path A
    
    # GOP interval in seconds (used to calculate gop = framerate * interval)
    gop_interval_seconds: float = 1.0
    
    # Video settings
    bitrate_kbps: int = 20000  # 20 Mbps default
    rc_mode: int = 1  # 0=VBR, 1=CBR, 2=FixQP (CBR default)
    
    # Audio settings
    audio_source: AudioSource = AudioSource.HDMI_RX
    
    # Streaming (always enabled)
    srt_port: int = 8888
    
    # Recording (optional)
    recording_enabled: bool = False
    recording_path: str = "/mnt/sdcard/recordings/capture.ts"
    
    # HDR mode
    use_hdr: bool = True  # When True and source is HDR 10-bit, use HDR pipeline
    
    # Auto-start behavior
    autostart_on_ready: bool = True
    
    # Runtime info (from HDMI TX detection)
    width: int = 3840
    height: int = 2160
    framerate: int = 60
    
    # Runtime info (from HDMI RX detection)
    source_is_hdr: bool = False
    source_color_depth: int = 8
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data["audio_source"] = self.audio_source.value
        data["capture_source"] = self.capture_source.value
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutoInstanceConfig":
        """Create config from dictionary."""
        if "audio_source" in data and isinstance(data["audio_source"], str):
            data["audio_source"] = AudioSource(data["audio_source"])
        if "capture_source" in data and isinstance(data["capture_source"], str):
            data["capture_source"] = CaptureSource(data["capture_source"])
        # Filter out unknown fields
        valid_fields = cls.__dataclass_fields__.keys()
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)


class PipelineBuilder:
    """Builds GStreamer pipeline for auto instance.
    
    Supports three capture source modes:
    
    1. streamboxsrc source=vfmcap (Path A) — Raw vfm_cap capture with Vulkan
       GPU format conversion.  Always P010 for HDR, NV12 for SDR.
       
    2. streamboxsrc source=vdin1 (Path B) — VPP color-processed capture.
       NV21 passthrough for 8-bit SDR, Vulkan AMLY→P010 for 10-bit HDR.
       
    3. v4l2src device=/dev/video71 (Legacy) — Deprecated vdin1 v4l2 capture.
       NV21 for SDR, ENCODED format for HDR.
    
    All modes use:
    - amlvenc (H.265 hardware encoder)
    - alsasrc for audio (HDMI RX or Line In)
    - mpegtsmux for muxing
    - srtsink for streaming + optional filesink for recording
    """
    
    def build(self, config: AutoInstanceConfig) -> str:
        """Build complete pipeline string.
        
        Args:
            config: Auto instance configuration
            
        Returns:
            Complete gst-launch-1.0 pipeline string
        """
        # Calculate GOP from framerate and interval
        gop = int(config.framerate * config.gop_interval_seconds)
        
        audio_device = "hw:0,6" if config.audio_source == AudioSource.HDMI_RX else "hw:0,0"
        
        # Determine if we should use HDR 10-bit pipeline.
        # When the user explicitly enables use_hdr, trust their choice and
        # generate the HDR pipeline regardless of live source detection.
        # Source auto-detection (source_is_hdr / source_color_depth) only
        # matters for the auto-start path in on_passthrough_ready().
        use_hdr_pipeline = config.use_hdr
        
        # Build video source based on capture source selection
        if config.capture_source == CaptureSource.VFMCAP:
            pipeline = self._build_vfmcap_source(config, gop, use_hdr_pipeline)
        elif config.capture_source == CaptureSource.VDIN1:
            pipeline = self._build_vdin1_source(config, gop, use_hdr_pipeline)
        else:
            pipeline = self._build_v4l2_legacy_source(config, gop, use_hdr_pipeline)
        
        # Audio branch (same for all capture modes)
        pipeline += (
            f'alsasrc device={audio_device} buffer-time=500000 provide-clock=false '
            f'slave-method=re-timestamp ! '
            f'audio/x-raw,rate=48000,channels=2,format=S16LE ! '
            f'queue max-size-buffers=0 max-size-time=500000000 max-size-bytes=0 ! '
            f'audioconvert ! audioresample ! avenc_aac bitrate=128000 ! aacparse ! '
            f'queue max-size-buffers=0 max-size-time=500000000 max-size-bytes=0 ! '
            f'mux. '
            # Muxer definition
            f'mpegtsmux name=mux alignment=7 latency=100000000'
        )
        
        # Output
        if config.recording_enabled:
            # Both recording and streaming - use tee
            pipeline += (
                f' ! tee name=t '
                f't. ! queue ! filesink location="{config.recording_path}" '
                f't. ! queue ! srtsink uri="srt://:{config.srt_port}" '
                f'wait-for-connection=false latency=600 sync=false'
            )
        else:
            # Streaming only
            pipeline += (
                f' ! srtsink uri="srt://:{config.srt_port}" '
                f'wait-for-connection=false latency=600 sync=false'
            )
        
        return pipeline
    
    def _build_vfmcap_source(
        self, config: AutoInstanceConfig, gop: int, use_hdr: bool
    ) -> str:
        """Build Path A: streamboxsrc source=vfmcap pipeline.
        
        Path A always outputs P010_10LE for HDR (Vulkan GPU conversion from
        raw vfm_cap formats).  For SDR, outputs NV12.
        """
        if use_hdr:
            # HDR 10-bit: P010_10LE via Vulkan conversion
            return (
                f'streamboxsrc source=vfmcap output-format=p010 ! '
                f'video/x-raw,format=P010_10LE,width={config.width},height={config.height},'
                f'framerate={config.framerate}/1 ! '
                f'amlvenc internal-bit-depth=10 '
                f'gop={gop} gop-pattern=0 bitrate={config.bitrate_kbps} '
                f'framerate={config.framerate} rc-mode={config.rc_mode} ! '
                f'video/x-h265 ! '
                f'h265parse config-interval=-1 ! '
                f'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! '
                f'mux. '
            )
        else:
            # SDR 8-bit: NV12
            return (
                f'streamboxsrc source=vfmcap output-format=nv12 ! '
                f'video/x-raw,format=NV12,width={config.width},height={config.height},'
                f'framerate={config.framerate}/1 ! '
                f'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! '
                f'amlvenc gop={gop} gop-pattern=0 framerate={config.framerate} '
                f'bitrate={config.bitrate_kbps} rc-mode={config.rc_mode} ! '
                f'video/x-h265 ! '
                f'h265parse config-interval=-1 ! '
                f'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! '
                f'mux. '
            )
    
    def _build_vdin1_source(
        self, config: AutoInstanceConfig, gop: int, use_hdr: bool
    ) -> str:
        """Build Path B: streamboxsrc source=vdin1 pipeline.
        
        Path B auto-detects signal depth.  For 8-bit SDR, outputs NV21
        passthrough.  For 10-bit HDR, Vulkan converts AMLY→P010.
        """
        if use_hdr:
            # HDR 10-bit: Vulkan AMLY→P010 conversion
            return (
                f'streamboxsrc source=vdin1 output-format=p010 ! '
                f'video/x-raw,format=P010_10LE,width={config.width},height={config.height},'
                f'framerate={config.framerate}/1 ! '
                f'amlvenc internal-bit-depth=10 '
                f'gop={gop} gop-pattern=0 bitrate={config.bitrate_kbps} '
                f'framerate={config.framerate} rc-mode={config.rc_mode} ! '
                f'video/x-h265 ! '
                f'h265parse config-interval=-1 ! '
                f'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! '
                f'mux. '
            )
        else:
            # SDR 8-bit: NV21 passthrough (no GPU conversion needed)
            return (
                f'streamboxsrc source=vdin1 ! '
                f'video/x-raw,format=NV21,width={config.width},height={config.height},'
                f'framerate={config.framerate}/1 ! '
                f'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! '
                f'amlvenc gop={gop} gop-pattern=0 framerate={config.framerate} '
                f'bitrate={config.bitrate_kbps} rc-mode={config.rc_mode} ! '
                f'video/x-h265 ! '
                f'h265parse config-interval=-1 ! '
                f'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! '
                f'mux. '
            )
    
    def _build_v4l2_legacy_source(
        self, config: AutoInstanceConfig, gop: int, use_hdr: bool
    ) -> str:
        """Build Legacy: v4l2src device=/dev/video71 pipeline (deprecated).
        
        Original v4l2 capture path.  Uses ENCODED format for HDR, NV21 for SDR.
        Kept for backward compatibility but deprecated in favor of streamboxsrc.
        """
        if use_hdr:
            # HDR 10-bit pipeline: ENCODED format + internal-bit-depth=10 + Vulkan backend
            return (
                f'v4l2src device=/dev/video71 io-mode=dmabuf do-timestamp=true ! '
                f'video/x-raw,format=ENCODED,width={config.width},height={config.height},'
                f'framerate={config.framerate}/1 ! '
                f'videorate ! '
                f'amlvenc internal-bit-depth=10 v10conv-backend=0 '
                f'gop={gop} gop-pattern=0 bitrate={config.bitrate_kbps} '
                f'framerate={config.framerate} rc-mode={config.rc_mode} ! '
                f'video/x-h265 ! '
                f'h265parse config-interval=-1 ! '
                f'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! '
                f'mux. '
            )
        else:
            # SDR 8-bit pipeline: NV21 format (standard)
            return (
                f'v4l2src device=/dev/video71 io-mode=dmabuf do-timestamp=true ! '
                f'video/x-raw,format=NV21,width={config.width},height={config.height},'
                f'framerate={config.framerate}/1 ! '
                f'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! '
                f'amlvenc gop={gop} gop-pattern=0 framerate={config.framerate} '
                f'bitrate={config.bitrate_kbps} rc-mode={config.rc_mode} ! '
                f'video/x-h265 ! '
                f'h265parse config-interval=-1 ! '
                f'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! '
                f'mux. '
            )
    
    def build_preview(self, config: AutoInstanceConfig) -> str:
        """Build pipeline preview with line breaks for readability."""
        pipeline = self.build(config)
        # Add line breaks after each element
        return pipeline.replace(' ! ', ' ! \\\n   ')


class AutoInstanceManager:
    """Manages the single auto instance.
    
    Only one auto instance is allowed per system. Creating a new one
    will replace the existing instance.
    
    Auto-creates with default settings on first boot if no config exists.
    """
    
    CONFIG_FILE = Path("/var/lib/gst-manager/auto_instance.json")
    
    # Default configuration for out-of-box experience
    DEFAULT_CONFIG = AutoInstanceConfig(
        capture_source=CaptureSource.VFMCAP,  # Path A: raw/low-latency
        gop_interval_seconds=1.0,
        bitrate_kbps=20000,
        rc_mode=1,  # CBR
        audio_source=AudioSource.HDMI_RX,
        srt_port=8888,
        recording_enabled=False,
        recording_path="/mnt/sdcard/recordings/capture.ts",
        use_hdr=True,  # Use HDR 10-bit when source is HDR
        autostart_on_ready=True  # Key: auto-start when HDMI ready
    )
    
    def __init__(self, instance_manager, event_manager=None):
        """Initialize auto instance manager.
        
        Args:
            instance_manager: InstanceManager for creating/managing instances
            event_manager: EventManager for HDMI state callbacks (set later)
        """
        self.instance_manager = instance_manager
        self.event_manager = event_manager
        self.config: Optional[AutoInstanceConfig] = None
        self.instance_id: Optional[str] = None
        self._builder = PipelineBuilder()
        
    async def load(self) -> bool:
        """Initialize auto instance configuration.
        
        Always uses default settings - no config file required.
        Settings can be updated via D-Bus and are persisted for next boot.
        
        Returns:
            True if config is ready
        """
        # Always start with default config
        self.config = self.DEFAULT_CONFIG
        
        # Try to load user customizations if they exist
        if self.CONFIG_FILE.exists():
            try:
                with open(self.CONFIG_FILE, "r") as f:
                    data = json.load(f)
                
                # Merge user settings with defaults
                user_config = data.get("config", {})
                if user_config:
                    self.config = AutoInstanceConfig.from_dict(user_config)
                    logger.info("Loaded user customizations from config file")
                
                # Remember instance ID if exists
                self.instance_id = data.get("instance_id")
                
            except Exception as e:
                logger.warning(f"Could not load config file, using defaults: {e}")
        else:
            logger.info("No config file found, using default settings")
        
        return True
    
    async def save(self) -> bool:
        """Save auto instance configuration to disk.
        
        Returns:
            True if saved successfully
        """
        try:
            data = {
                "config": self.config.to_dict() if self.config else {},
                "instance_id": self.instance_id
            }
            
            self.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=2)
            
            logger.debug("Saved auto instance config")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save auto instance config: {e}")
            return False
    
    async def create_or_update(
        self,
        config: AutoInstanceConfig,
        hdmi_tx_status: Optional[Any] = None
    ) -> str:
        """Create or update the auto instance.
        
        Only one auto instance is allowed. Creating a new one will
        delete and replace the existing instance.
        
        Args:
            config: New configuration
            hdmi_tx_status: Current HDMI TX status for resolution detection
            
        Returns:
            instance_id: The auto instance ID
        """
        # Update config with current HDMI TX info if available
        if hdmi_tx_status:
            config.width = hdmi_tx_status.width or config.width
            config.height = hdmi_tx_status.height or config.height
            config.framerate = hdmi_tx_status.fps or config.framerate
        
        # Update HDR info from event manager's RX status
        if self.event_manager:
            rx_status = self.event_manager.get_hdmi_status()
            config.source_color_depth = rx_status.get("color_depth", 8)
            config.source_is_hdr = rx_status.get("hdr_info", 0) > 0
        
        self.config = config
        
        # Generate pipeline
        pipeline = self._builder.build(config)
        
        # Delete existing auto instance if present
        if self.instance_id:
            try:
                existing = self.instance_manager.get_instance(self.instance_id)
                if existing:
                    if existing.status.value == "running":
                        logger.info(f"Stopping existing auto instance: {self.instance_id}")
                        await self.instance_manager.stop_instance(self.instance_id)
                    logger.info(f"Deleting existing auto instance: {self.instance_id}")
                    await self.instance_manager.delete_instance(self.instance_id)
            except Exception as e:
                logger.warning(f"Error cleaning up existing instance: {e}")
        
        # Create new auto instance
        from instances import InstanceType
        
        instance_id = await self.instance_manager.create_instance(
            name="Auto HDMI Capture",
            pipeline=pipeline
        )
        
        # Mark as auto instance with configuration
        instance = self.instance_manager.get_instance(instance_id)
        if instance:
            instance.instance_type = InstanceType.AUTO
            instance.auto_config = config.to_dict()
            instance.autostart = config.autostart_on_ready
            instance.trigger_event = "hdmi_passthrough_ready"
            logger.info(f"Marked instance {instance_id} as AUTO type, autostart={instance.autostart}")
            
            # Re-save to persist the instance_type change
            await self.instance_manager.history_manager.save_instance(instance.to_dict())
        
        self.instance_id = instance_id
        await self.save()
        
        logger.info(f"Created auto instance: {instance_id}")
        return instance_id
    
    def get_pipeline_preview(self, config: AutoInstanceConfig) -> str:
        """Get pipeline preview without creating instance.
        
        Args:
            config: Configuration to preview
            
        Returns:
            Formatted pipeline string with line breaks
        """
        return self._builder.build_preview(config)
    
    async def on_passthrough_ready(self, hdmi_tx_status: Any) -> None:
        """Called when HDMI passthrough becomes ready.
        
        Starts the auto instance if autostart is enabled.
        
        Args:
            hdmi_tx_status: Current HDMI TX status
        """
        if not self.config:
            logger.debug("No auto instance config, skipping passthrough ready")
            return
        
        if not self.config.autostart_on_ready:
            logger.debug("Auto-start disabled")
            return
        
        # Update resolution from current TX status
        self.config.width = hdmi_tx_status.width or self.config.width
        self.config.height = hdmi_tx_status.height or self.config.height
        self.config.framerate = hdmi_tx_status.fps or self.config.framerate
        
        # Update HDR info from event manager's RX status
        source_is_hdr = False
        if self.event_manager:
            rx_status = self.event_manager.get_hdmi_status()
            self.config.source_color_depth = rx_status.get("color_depth", 8)
            source_is_hdr = rx_status.get("hdr_info", 0) > 0
            self.config.source_is_hdr = source_is_hdr
        
        # For auto-start, gate HDR on actual source capability:
        # user wants HDR (use_hdr=True) but source is SDR -> use SDR pipeline.
        # We temporarily adjust use_hdr for pipeline generation, then restore it
        # so the saved preference is preserved.
        original_use_hdr = self.config.use_hdr
        if self.config.use_hdr and not source_is_hdr:
            logger.info("Auto-start: source is not HDR, falling back to SDR pipeline")
            self.config.use_hdr = False

        desired_config = AutoInstanceConfig.from_dict(self.config.to_dict())
        desired_pipeline = self._builder.build(desired_config)
        
        # Recreate the auto instance whenever the desired HDMI-driven pipeline
        # differs from the currently managed one. This is critical for HDMI
        # mode changes where passthrough stays logically ready but the old
        # pipeline exits shortly afterward due to a resolution/framerate change.
        if self.instance_id:
            instance = self.instance_manager.get_instance(self.instance_id)
            if instance and instance.pipeline != desired_pipeline:
                logger.info(
                    "Auto instance pipeline no longer matches current HDMI state; "
                    "recreating for new parameters"
                )
                await self.create_or_update(self.config, hdmi_tx_status)
            elif instance and instance.status.value in ("stopped", "error"):
                logger.info(f"Recreating auto instance (was {instance.status.value}) with updated resolution")
                await self.create_or_update(self.config, hdmi_tx_status)
            elif not instance:
                # Instance was deleted externally
                logger.info("Auto instance was deleted, creating new one")
                self.instance_id = None
                await self.create_or_update(self.config, hdmi_tx_status)
        else:
            # Create new instance
            logger.info("Creating auto instance for passthrough")
            await self.create_or_update(self.config, hdmi_tx_status)
        
        # Restore the user's HDR preference so it's not lost in saved config
        self.config.use_hdr = original_use_hdr
        if original_use_hdr and not source_is_hdr:
            # Re-save with the restored preference so it persists correctly
            await self.save()
        
        # Start the instance
        if self.instance_id:
            try:
                # Wait for vdin1/VPP writeback to fully stabilize after HDMI reconnect.
                # The TX check delay (1.5s) may not be enough for the capture path.
                await asyncio.sleep(2.0)
                
                # Verify passthrough is still valid (signal didn't drop again)
                if self.event_manager:
                    state = self.event_manager.get_passthrough_state()
                    if not state.get("can_capture"):
                        logger.warning("Passthrough lost during stabilization delay, aborting start")
                        return
                
                instance = self.instance_manager.get_instance(self.instance_id)
                if not instance:
                    logger.warning("Auto instance disappeared before start")
                    return

                if instance.status.value == "running":
                    logger.info(f"Auto instance {self.instance_id} already running with current pipeline")
                    return

                await self.instance_manager.start_instance(self.instance_id)
                logger.info(f"Auto-started instance {self.instance_id}")
            except Exception as e:
                logger.error(f"Failed to auto-start instance: {e}")
    
    async def on_passthrough_lost(self) -> None:
        """Called when HDMI passthrough is lost.
        
        Stops the auto instance if it's running, and ensures it ends up
        in 'stopped' state (not 'error') so it can be restarted later.
        """
        if not self.instance_id:
            return
        
        instance = self.instance_manager.get_instance(self.instance_id)
        if not instance:
            return
        
        if instance.status.value == "running":
            try:
                await self.instance_manager.stop_instance(self.instance_id)
                logger.info(f"Auto-stopped instance {self.instance_id} due to passthrough loss")
            except Exception as e:
                logger.error(f"Failed to auto-stop instance: {e}")
        
        # Ensure the instance is in 'stopped' state regardless.
        # The gst-launch process may have already crashed with an error
        # (e.g. v4l2src lost signal) before we got here, leaving the
        # instance in 'error' state. Force it to 'stopped' so
        # on_passthrough_ready() can restart it cleanly.
        instance = self.instance_manager.get_instance(self.instance_id)
        if instance and instance.status.value == "error":
            logger.info(f"Clearing error state for instance {self.instance_id} (passthrough lost)")
            from instances import InstanceStatus
            instance.status = InstanceStatus.STOPPED
            instance.error_message = None
            instance.retry_count = 0
    
    def get_config(self) -> Optional[Dict[str, Any]]:
        """Get current config as dict.
        
        Returns:
            Config dictionary or None if not configured
        """
        if self.config:
            return self.config.to_dict()
        return None
    
    async def update_config(self, updates: Dict[str, Any]) -> bool:
        """Update configuration (preserves instance if stopped).
        
        Args:
            updates: Dictionary of config fields to update
            
        Returns:
            True if updated successfully
        """
        if not self.config:
            return False
        
        # Apply updates
        if "capture_source" in updates:
            self.config.capture_source = CaptureSource(updates["capture_source"])
        if "gop_interval_seconds" in updates:
            self.config.gop_interval_seconds = float(updates["gop_interval_seconds"])
        if "bitrate_kbps" in updates:
            self.config.bitrate_kbps = int(updates["bitrate_kbps"])
        if "rc_mode" in updates:
            self.config.rc_mode = int(updates["rc_mode"])
        if "audio_source" in updates:
            self.config.audio_source = AudioSource(updates["audio_source"])
        if "srt_port" in updates:
            self.config.srt_port = int(updates["srt_port"])
        if "recording_enabled" in updates:
            self.config.recording_enabled = bool(updates["recording_enabled"])
        if "recording_path" in updates:
            self.config.recording_path = updates["recording_path"]
        if "autostart_on_ready" in updates:
            self.config.autostart_on_ready = bool(updates["autostart_on_ready"])
        if "use_hdr" in updates:
            self.config.use_hdr = bool(updates["use_hdr"])
        
        # Recreate instance with new pipeline if it exists and is stopped
        if self.instance_id:
            instance = self.instance_manager.get_instance(self.instance_id)
            if instance and instance.status.value == "stopped":
                await self.create_or_update(self.config)
        
        await self.save()
        return True
    
    async def delete(self) -> bool:
        """Delete the auto instance and config.
        
        Returns:
            True if deleted successfully
        """
        if self.instance_id:
            try:
                instance = self.instance_manager.get_instance(self.instance_id)
                if instance:
                    if instance.status.value == "running":
                        await self.instance_manager.stop_instance(self.instance_id)
                    await self.instance_manager.delete_instance(self.instance_id)
            except Exception as e:
                logger.error(f"Error deleting instance: {e}")
        
        self.instance_id = None
        self.config = None
        
        # Remove config file
        try:
            if self.CONFIG_FILE.exists():
                self.CONFIG_FILE.unlink()
        except Exception as e:
            logger.error(f"Error removing config file: {e}")
        
        return True
