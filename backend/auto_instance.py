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
import subprocess
import time
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Optional, Dict, Any

from instances import InstanceStatus

logger = logging.getLogger("gst-manager.auto_instance")

_AMLVENC_QP_PROPERTY_CACHE: Optional[bool] = None


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


class OutputCodec(Enum):
    """Encoder output codec options."""

    H265 = "h265"
    H264 = "h264"


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
    output_codec: OutputCodec = OutputCodec.H265
    bitrate_kbps: int = 20000  # 20 Mbps default
    rc_mode: int = 1  # 0=VBR, 1=CBR, 2=FixQP (CBR default)
    gop_pattern: int = 0  # Wave521 GOP preset
    lossless_enable: bool = False
    fixed_qp_value: int = 28
    
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

    # Restart behavior
    signal_debounce_seconds: float = 2.0
    max_restart_retries: int = 5
    restart_backoff_base: float = 1.0
    restart_backoff_max: float = 30.0
    
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
        data["output_codec"] = self.output_codec.value
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutoInstanceConfig":
        """Create config from dictionary."""
        if "audio_source" in data and isinstance(data["audio_source"], str):
            data["audio_source"] = AudioSource(data["audio_source"])
        if "capture_source" in data and isinstance(data["capture_source"], str):
            data["capture_source"] = CaptureSource(data["capture_source"])
        if "output_codec" in data and isinstance(data["output_codec"], str):
            data["output_codec"] = OutputCodec(data["output_codec"])
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
        
        codec_caps, codec_parser = self._build_codec_output(config)

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

    def _build_codec_output(self, config: AutoInstanceConfig) -> tuple[str, str]:
        """Build output caps and parser for the selected codec."""
        if config.output_codec == OutputCodec.H264:
            return ("video/x-h264", "h264parse config-interval=-1")
        return ("video/x-h265", "h265parse config-interval=-1")
    
    def _build_vfmcap_source(
        self, config: AutoInstanceConfig, gop: int, use_hdr: bool
    ) -> str:
        """Build Path A: streamboxsrc source=vfmcap pipeline.
        
        Path A always outputs P010_10LE for HDR (Vulkan GPU conversion from
        raw vfm_cap formats).  For SDR, outputs NV12.
        """
        codec_caps, codec_parser = self._build_codec_output(config)
        if use_hdr:
            # HDR 10-bit: P010_10LE via Vulkan conversion
            return (
                f'streamboxsrc source=vfmcap output-format=p010 ! '
                f'video/x-raw,format=P010_10LE,width={config.width},height={config.height},'
                f'framerate={config.framerate}/1 ! '
                f'queue max-size-buffers=5 max-size-time=0 max-size-bytes=0 ! '
                f'{self._build_encoder_settings(config, gop, hdr=True)} ! '
                f'{codec_caps} ! '
                f'{codec_parser} ! '
                f'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! '
                f'mux. '
            )
        else:
            # SDR 8-bit: NV12
            return (
                f'streamboxsrc source=vfmcap output-format=nv12 ! '
                f'video/x-raw,format=NV12,width={config.width},height={config.height},'
                f'framerate={config.framerate}/1 ! '
                f'queue max-size-buffers=5 max-size-time=0 max-size-bytes=0 ! '
                f'{self._build_encoder_settings(config, gop, hdr=False)} ! '
                f'{codec_caps} ! '
                f'{codec_parser} ! '
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
        codec_caps, codec_parser = self._build_codec_output(config)
        if use_hdr:
            # HDR 10-bit: Vulkan AMLY→P010 conversion
            return (
                f'streamboxsrc source=vdin1 output-format=p010 ! '
                f'video/x-raw,format=P010_10LE,width={config.width},height={config.height},'
                f'framerate={config.framerate}/1 ! '
                f'queue max-size-buffers=5 max-size-time=0 max-size-bytes=0 ! '
                f'{self._build_encoder_settings(config, gop, hdr=True)} ! '
                f'{codec_caps} ! '
                f'{codec_parser} ! '
                f'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! '
                f'mux. '
            )
        else:
            # SDR 8-bit: NV21 passthrough (no GPU conversion needed)
            return (
                f'streamboxsrc source=vdin1 ! '
                f'video/x-raw,format=NV21,width={config.width},height={config.height},'
                f'framerate={config.framerate}/1 ! '
                f'queue max-size-buffers=5 max-size-time=0 max-size-bytes=0 ! '
                f'{self._build_encoder_settings(config, gop, hdr=False)} ! '
                f'{codec_caps} ! '
                f'{codec_parser} ! '
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
        codec_caps, codec_parser = self._build_codec_output(config)
        if use_hdr:
            # HDR 10-bit pipeline: ENCODED format + internal-bit-depth=10 + Vulkan backend
            return (
                f'v4l2src device=/dev/video71 io-mode=dmabuf do-timestamp=true ! '
                f'video/x-raw,format=ENCODED,width={config.width},height={config.height},'
                f'framerate={config.framerate}/1 ! '
                f'videorate ! '
                f'queue max-size-buffers=5 max-size-time=0 max-size-bytes=0 ! '
                f'{self._build_encoder_settings(config, gop, hdr=True, legacy_v4l2=True)} ! '
                f'{codec_caps} ! '
                f'{codec_parser} ! '
                f'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! '
                f'mux. '
            )
        else:
            # SDR 8-bit pipeline: NV21 format (standard)
            return (
                f'v4l2src device=/dev/video71 io-mode=dmabuf do-timestamp=true ! '
                f'video/x-raw,format=NV21,width={config.width},height={config.height},'
                f'framerate={config.framerate}/1 ! '
                f'queue max-size-buffers=5 max-size-time=0 max-size-bytes=0 ! '
                f'{self._build_encoder_settings(config, gop, hdr=False)} ! '
                f'{codec_caps} ! '
                f'{codec_parser} ! '
                f'queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! '
                f'mux. '
            )

    def _build_encoder_settings(
        self,
        config: AutoInstanceConfig,
        gop: int,
        hdr: bool,
        legacy_v4l2: bool = False,
    ) -> str:
        """Build amlvenc settings string for the selected encoder mode."""
        lossless_enabled = config.lossless_enable and config.output_codec == OutputCodec.H265
        settings = ["amlvenc"]
        if hdr:
            settings.append("internal-bit-depth=10")
        if legacy_v4l2 and hdr:
            settings.append("v10conv-backend=0")

        settings.extend([
            f"gop={gop}",
            f"gop-pattern={config.gop_pattern}",
            f"framerate={config.framerate}",
        ])

        if lossless_enabled:
            settings.append("lossless-enable=true")
        else:
            settings.extend([
                f"bitrate={config.bitrate_kbps}",
                f"rc-mode={config.rc_mode}",
            ])
            if config.rc_mode == 2:
                if self._supports_full_fixed_qp():
                    settings.extend([
                        f"qp-i={config.fixed_qp_value}",
                        f"qp-p={config.fixed_qp_value}",
                        f"qp-b={config.fixed_qp_value}",
                    ])
                else:
                    settings.append(f"qp-b={config.fixed_qp_value}")

        return " ".join(settings)

    def _supports_full_fixed_qp(self) -> bool:
        """Check whether the installed amlvenc plugin supports qp-i/qp-p."""
        global _AMLVENC_QP_PROPERTY_CACHE
        if _AMLVENC_QP_PROPERTY_CACHE is not None:
            return _AMLVENC_QP_PROPERTY_CACHE

        try:
            result = subprocess.run(
                ["gst-inspect-1.0", "amlvenc"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            text = result.stdout + result.stderr
            _AMLVENC_QP_PROPERTY_CACHE = "qp-i" in text and "qp-p" in text
        except Exception as e:
            logger.warning("Failed to inspect amlvenc capabilities: %s", e)
            _AMLVENC_QP_PROPERTY_CACHE = False

        if not _AMLVENC_QP_PROPERTY_CACHE:
            logger.warning(
                "Installed amlvenc does not expose qp-i/qp-p yet; Fix QP falls back to qp-b only"
            )

        return _AMLVENC_QP_PROPERTY_CACHE
    
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
        output_codec=OutputCodec.H265,
        bitrate_kbps=20000,
        rc_mode=1,  # CBR
        gop_pattern=0,
        lossless_enable=False,
        fixed_qp_value=28,
        audio_source=AudioSource.HDMI_RX,
        srt_port=8888,
        recording_enabled=False,
        recording_path="/mnt/sdcard/recordings/capture.ts",
        use_hdr=True,  # Use HDR 10-bit when source is HDR
        autostart_on_ready=True  # Key: auto-start when HDMI ready
    )

    DEFAULT_RECORDING_PATH = "/mnt/sdcard/recordings/"
    
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
        self._restart_task: Optional[asyncio.Task] = None
        self._restart_generation = 0

        if hasattr(self.instance_manager, "add_exit_callback"):
            self.instance_manager.add_exit_callback(self.on_instance_exit)
        
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
        runtime_status: Optional[Any] = None
    ) -> str:
        """Create or update the auto instance.
        
        Only one auto instance is allowed. Creating a new one will
        delete and replace the existing instance.
        
        Args:
            config: New configuration
            runtime_status: Current runtime signal status for parameter detection
            
        Returns:
            instance_id: The auto instance ID
        """
        self._prepare_recording_path(config)
        self._apply_runtime_status_to_config(config, runtime_status)
        
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
            instance.trigger_event = (
                "hdmi_passthrough_ready"
                if config.capture_source == CaptureSource.VDIN1
                else "hdmi_signal_ready"
            )
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
        """Start TX-dependent auto capture when passthrough is ready."""
        if self._requires_tx_dependency():
            await self._ensure_auto_instance_running(
                trigger="passthrough_ready",
                runtime_status=hdmi_tx_status,
            )

    async def on_hdmi_signal_ready(self, hdmi_status: Any) -> None:
        """Start RX-driven capture paths when HDMI RX becomes stable."""
        if not self._requires_tx_dependency():
            await self._ensure_auto_instance_running(
                trigger="hdmi_signal_ready",
                runtime_status=hdmi_status,
            )

    async def on_instance_exit(self, exit_info: Any) -> None:
        """Handle auto-instance process exits and schedule recovery."""
        if not self.config or not self.instance_id:
            return
        if exit_info.instance_id != self.instance_id:
            return

        if exit_info.intentional:
            self._cancel_restart_task()
            return

        signal_change = getattr(exit_info, "signal_change", None)
        if signal_change and signal_change.reason == "signal-lost":
            await self._mark_waiting_for_signal(signal_change.reason)
            self._cancel_restart_task()
            return

        if signal_change:
            logger.info(
                "Scheduling auto restart after %ss for reason=%s",
                self.config.signal_debounce_seconds,
                signal_change.reason,
            )
            self._schedule_restart(
                reason=signal_change.reason,
                signal_info=signal_change.to_dict(),
                attempt=0,
                use_debounce=True,
            )
            return

        logger.warning(
            "Auto instance %s exited unexpectedly (code=%s), scheduling retry",
            exit_info.instance_id,
            exit_info.exit_code,
        )
        self._schedule_restart(
            reason="process-exit",
            signal_info=None,
            attempt=0,
            use_debounce=False,
        )

    async def _ensure_auto_instance_running(
        self,
        trigger: str,
        runtime_status: Optional[Any] = None,
    ) -> None:
        """Ensure the auto instance matches the current signal and is running."""
        if not self.config:
            logger.debug("No auto instance config, skipping %s", trigger)
            return
        if not self.config.autostart_on_ready:
            logger.debug("Auto-start disabled")
            return
        if not self._can_start_for_current_source():
            logger.debug("Auto instance not ready to start for trigger=%s", trigger)
            return

        self._cancel_restart_task()

        desired_config = AutoInstanceConfig.from_dict(self.config.to_dict())
        self._apply_runtime_status_to_config(desired_config, runtime_status)
        self._apply_event_manager_status(desired_config)

        original_use_hdr = desired_config.use_hdr
        if desired_config.use_hdr and not desired_config.source_is_hdr:
            logger.info("Auto-start: source is not HDR, falling back to SDR pipeline")
            desired_config.use_hdr = False

        if desired_config.lossless_enable and desired_config.output_codec != OutputCodec.H265:
            logger.warning("Lossless encoding requires H.265, disabling lossless for this pipeline")
            desired_config.lossless_enable = False

        desired_pipeline = self._builder.build(desired_config)

        if self.instance_id:
            instance = self.instance_manager.get_instance(self.instance_id)
            if instance and instance.pipeline != desired_pipeline:
                logger.info(
                    "Auto instance pipeline no longer matches current signal; recreating"
                )
                await self.create_or_update(desired_config, runtime_status)
            elif instance and instance.status.value in (
                "stopped",
                "error",
                "waiting_signal",
            ):
                logger.info(
                    "Recreating auto instance (was %s) for trigger=%s",
                    instance.status.value,
                    trigger,
                )
                await self.create_or_update(desired_config, runtime_status)
            elif not instance:
                logger.info("Auto instance disappeared, creating new one")
                self.instance_id = None
                await self.create_or_update(desired_config, runtime_status)
        else:
            logger.info("Creating auto instance for trigger=%s", trigger)
            await self.create_or_update(desired_config, runtime_status)

        self.config.use_hdr = original_use_hdr
        await self.save()

        if not self.instance_id:
            return

        try:
            stabilization_delay = 2.0 if self._requires_tx_dependency() else 0.0
            if stabilization_delay > 0:
                await asyncio.sleep(stabilization_delay)
            if not self._can_start_for_current_source():
                logger.warning("Capture readiness lost during stabilization delay")
                return

            instance = self.instance_manager.get_instance(self.instance_id)
            if not instance:
                logger.warning("Auto instance disappeared before start")
                return
            if instance.status == InstanceStatus.RUNNING:
                logger.info(
                    "Auto instance %s already running with current pipeline",
                    self.instance_id,
                )
                return

            await self.instance_manager.start_instance(self.instance_id)
            logger.info("Auto-started instance %s via %s", self.instance_id, trigger)
        except Exception as e:
            logger.error(f"Failed to auto-start instance: {e}")
            raise

    async def on_passthrough_lost(self) -> None:
        """Stop TX-dependent auto capture when passthrough is lost."""
        if self._requires_tx_dependency():
            await self._handle_capture_lost("passthrough lost")

    async def on_hdmi_signal_lost(self) -> None:
        """Stop RX-driven auto capture when HDMI RX is lost."""
        if not self._requires_tx_dependency():
            await self._handle_capture_lost("hdmi signal lost")

    async def _handle_capture_lost(self, reason: str) -> None:
        """Stop the auto instance when its capture source becomes unavailable."""
        self._cancel_restart_task()
        if not self.instance_id:
            return

        instance = self.instance_manager.get_instance(self.instance_id)
        if not instance:
            return
        if instance.status.value == "running":
            try:
                await self.instance_manager.stop_instance(self.instance_id)
                logger.info("Auto-stopped instance %s due to %s", self.instance_id, reason)
            except Exception as e:
                logger.error(f"Failed to auto-stop instance: {e}")

        await self._mark_waiting_for_signal(reason)

    async def _mark_waiting_for_signal(self, reason: str) -> None:
        """Move the current auto instance into waiting-signal state."""
        if not self.instance_id:
            return

        await self.instance_manager.set_instance_status(
            self.instance_id,
            InstanceStatus.WAITING_SIGNAL,
            error_message=reason,
        )

    def _cancel_restart_task(self) -> None:
        """Cancel any pending restart task."""
        if self._restart_task and not self._restart_task.done():
            self._restart_task.cancel()
        self._restart_task = None

    def _schedule_restart(
        self,
        reason: str,
        signal_info: Optional[Dict[str, Any]],
        attempt: int,
        use_debounce: bool,
    ) -> None:
        """Schedule an asynchronous restart attempt."""
        self._restart_generation += 1
        generation = self._restart_generation
        self._cancel_restart_task()
        self._restart_task = asyncio.create_task(
            self._restart_after_delay(
                generation=generation,
                reason=reason,
                signal_info=signal_info,
                attempt=attempt,
                use_debounce=use_debounce,
            )
        )

    async def _restart_after_delay(
        self,
        generation: int,
        reason: str,
        signal_info: Optional[Dict[str, Any]],
        attempt: int,
        use_debounce: bool,
    ) -> None:
        """Restart the auto instance after debounce or backoff delay."""
        try:
            if not self.config:
                return

            delay = 0.0
            if use_debounce:
                delay = max(0.0, float(self.config.signal_debounce_seconds))
            elif attempt > 0 or reason == "process-exit":
                delay = min(
                    float(self.config.restart_backoff_base) * (2 ** attempt),
                    float(self.config.restart_backoff_max),
                )
            if delay > 0:
                await asyncio.sleep(delay)

            if generation != self._restart_generation:
                return

            runtime_status = self._get_runtime_status_for_source(signal_info)
            if not self._can_start_for_current_source():
                await self._mark_waiting_for_signal(f"waiting after {reason}")
                return

            await self._ensure_auto_instance_running(
                trigger=f"restart:{reason}",
                runtime_status=runtime_status,
            )
        except asyncio.CancelledError:
            logger.debug("Auto restart task cancelled")
        except Exception as e:
            logger.error("Auto restart attempt failed: %s", e)
            if not self.config or attempt + 1 >= self.config.max_restart_retries:
                if self.instance_id:
                    await self.instance_manager.set_instance_status(
                        self.instance_id,
                        InstanceStatus.ERROR,
                        error_message=str(e),
                    )
                return
            self._schedule_restart(
                reason=reason,
                signal_info=signal_info,
                attempt=attempt + 1,
                use_debounce=False,
            )

    def _apply_event_manager_status(self, config: AutoInstanceConfig) -> None:
        """Update runtime fields from the latest event-manager state."""
        if not self.event_manager:
            return

        rx_status = self.event_manager.get_hdmi_status()
        config.source_color_depth = int(rx_status.get("color_depth", 8) or 8)
        config.source_is_hdr = bool(rx_status.get("hdr_info", 0) > 0)

        if not self._requires_tx_dependency():
            config.width = int(rx_status.get("width", config.width) or config.width)
            config.height = int(rx_status.get("height", config.height) or config.height)
            config.framerate = int(rx_status.get("fps", config.framerate) or config.framerate)

    def _apply_runtime_status_to_config(
        self,
        config: AutoInstanceConfig,
        runtime_status: Optional[Any],
    ) -> None:
        """Update runtime dimensions from a status mapping or object."""
        if not runtime_status:
            return

        def _value(name: str, default: int) -> int:
            if isinstance(runtime_status, dict):
                return int(runtime_status.get(name, default) or default)
            return int(getattr(runtime_status, name, default) or default)

        config.width = _value("width", config.width)
        config.height = _value("height", config.height)
        fps_value = _value("fps", config.framerate)
        if fps_value == config.framerate and isinstance(runtime_status, dict):
            fps_value = int(runtime_status.get("framerate", fps_value) or fps_value)
        config.framerate = fps_value

    def _requires_tx_dependency(self) -> bool:
        """Check whether the current capture source depends on HDMI TX."""
        if not self.config:
            return False
        return self.config.capture_source == CaptureSource.VDIN1

    def _can_start_for_current_source(self) -> bool:
        """Check whether the active capture path is ready to start."""
        if not self.event_manager or not self.config:
            return True

        if self._requires_tx_dependency():
            return bool(self.event_manager.get_passthrough_state().get("can_capture"))

        hdmi_status = self.event_manager.get_hdmi_status()
        return bool(
            hdmi_status.get("cable_connected", True)
            and hdmi_status.get("available", True)
            and hdmi_status.get("signal_locked")
            and hdmi_status.get("width", 0) > 0
            and hdmi_status.get("height", 0) > 0
        )

    def _get_runtime_status_for_source(
        self,
        signal_info: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        """Build runtime status from live state with signal-info fallback."""
        if self.event_manager and self.config:
            if self._requires_tx_dependency():
                state = self.event_manager.get_passthrough_state()
                if state.get("width") and state.get("height"):
                    return SimpleNamespace(
                        width=state.get("width", 0),
                        height=state.get("height", 0),
                        fps=state.get("framerate", 0),
                    )
            else:
                status = self.event_manager.get_hdmi_status()
                if status.get("width") and status.get("height"):
                    return status

        if signal_info:
            return {
                "width": signal_info.get("width", 0),
                "height": signal_info.get("height", 0),
                "fps": signal_info.get("fps", 0),
            }

        return None
    
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
        if "output_codec" in updates:
            self.config.output_codec = OutputCodec(updates["output_codec"])
        if "bitrate_kbps" in updates:
            self.config.bitrate_kbps = int(updates["bitrate_kbps"])
        if "rc_mode" in updates:
            self.config.rc_mode = int(updates["rc_mode"])
        if "gop_pattern" in updates:
            self.config.gop_pattern = int(updates["gop_pattern"])
        if "lossless_enable" in updates:
            self.config.lossless_enable = bool(updates["lossless_enable"])
        if "fixed_qp_value" in updates:
            self.config.fixed_qp_value = int(updates["fixed_qp_value"])
        if "audio_source" in updates:
            self.config.audio_source = AudioSource(updates["audio_source"])
        if "srt_port" in updates:
            self.config.srt_port = int(updates["srt_port"])
        if "recording_enabled" in updates:
            self.config.recording_enabled = bool(updates["recording_enabled"])
        if "recording_path" in updates:
            self.config.recording_path = updates["recording_path"]
        self._prepare_recording_path(self.config)
        if "autostart_on_ready" in updates:
            self.config.autostart_on_ready = bool(updates["autostart_on_ready"])
        if "use_hdr" in updates:
            self.config.use_hdr = bool(updates["use_hdr"])
        if "signal_debounce_seconds" in updates:
            self.config.signal_debounce_seconds = float(updates["signal_debounce_seconds"])
        if "max_restart_retries" in updates:
            self.config.max_restart_retries = int(updates["max_restart_retries"])
        if "restart_backoff_base" in updates:
            self.config.restart_backoff_base = float(updates["restart_backoff_base"])
        if "restart_backoff_max" in updates:
            self.config.restart_backoff_max = float(updates["restart_backoff_max"])
        
        # Recreate instance with new pipeline if it exists and is stopped
        if self.instance_id:
            instance = self.instance_manager.get_instance(self.instance_id)
            if instance and instance.status.value == "stopped":
                await self.create_or_update(self.config)
        
        await self.save()
        return True

    def _prepare_recording_path(self, config: AutoInstanceConfig) -> None:
        """Normalize recording path and create parent directory when needed."""
        normalized = (config.recording_path or "").strip() or self.DEFAULT_RECORDING_PATH
        path = Path(normalized).expanduser()

        if normalized.endswith("/") or path.suffix == "":
            timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
            path = path / f"capture-{timestamp}.ts"

        config.recording_path = str(path)

        if not config.recording_enabled:
            return

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("Failed to prepare recording directory %s: %s", path.parent, e)
    
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
