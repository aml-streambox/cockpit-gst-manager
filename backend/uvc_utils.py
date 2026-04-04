"""UVC Device Discovery and Management.

Handles USB Video Class device enumeration, capability detection,
and pipeline generation for UVC sources.
"""

import asyncio
import json
import logging
import os
import struct
import fcntl
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger("gst-manager.uvc")

# V4L2 constants
VIDIOC_QUERYCAP = 0x80685600
VIDIOC_ENUM_FMT = 0xc0405602
VIDIOC_S_FMT = 0xc0d05605
VIDIOC_G_FMT = 0xc0d05604
VIDIOC_REQBUFS = 0xc0145608
VIDIOC_QUERYBUF = 0xc0445609
VIDIOC_STREAMON = 0x40045612
VIDIOC_STREAMOFF = 0x40045613
VIDIOC_QBUF = 0xc044560f
VIDIOC_DQBUF = 0xc0445611
VIDIOC_ENUMINPUT = 0xc04c561a
VIDIOC_G_INPUT = 0x80045626
VIDIOC_S_INPUT = 0xc0045627

V4L2_CAP_VIDEO_CAPTURE = 0x00000001
V4L2_CAP_STREAMING = 0x04000000
V4L2_CAP_DEVICE_CAPS = 0x80000000
V4L2_BUF_TYPE_VIDEO_CAPTURE = 1

# UVC specific
UVC_FORMAT_MJPEG = "MJPG"
UVC_FORMAT_YUYV = "YUYV"
UVC_FORMAT_H264 = "H264"
UVC_FORMAT_YUY2 = "YUY2"


@dataclass
class FrameSize:
    """Represents a supported frame size."""
    width: int
    height: int
    min_fps: int
    max_fps: int
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class VideoFormat:
    """Represents a supported video format."""
    pixelformat: str
    description: str
    framesizes: List[FrameSize]
    
    def to_dict(self) -> Dict:
        return {
            "pixelformat": self.pixelformat,
            "description": self.description,
            "framesizes": [fs.to_dict() for fs in self.framesizes]
        }


@dataclass
class UVCDevice:
    """Represents a discovered UVC device."""
    device_path: str
    name: str
    bus_info: str
    driver: str
    is_uvc: bool
    formats: List[VideoFormat]
    serial: Optional[str] = None
    current_format: Optional[str] = None
    current_resolution: Optional[Tuple[int, int]] = None
    current_fps: Optional[int] = None
    
    def to_dict(self) -> Dict:
        return {
            "device_path": self.device_path,
            "name": self.name,
            "bus_info": self.bus_info,
            "driver": self.driver,
            "is_uvc": self.is_uvc,
            "serial": self.serial,
            "is_h264_passthrough": self.is_h264_passthrough,
            "is_mjpeg": self.is_mjpeg,
            "is_yuyv": self.is_yuyv,
            "formats": [f.to_dict() for f in self.formats],
            "current_format": self.current_format,
            "current_resolution": self.current_resolution,
            "current_fps": self.current_fps
        }
    
    @property
    def is_h264_passthrough(self) -> bool:
        """Check if device supports H.264 passthrough."""
        return any(f.pixelformat == UVC_FORMAT_H264 for f in self.formats)
    
    @property
    def is_mjpeg(self) -> bool:
        """Check if device supports MJPEG."""
        return any(f.pixelformat == UVC_FORMAT_MJPEG for f in self.formats)
    
    @property
    def is_yuyv(self) -> bool:
        """Check if device supports YUYV."""
        return any(f.pixelformat in (UVC_FORMAT_YUYV, UVC_FORMAT_YUY2) for f in self.formats)


class UVCDiscovery:
    """Discovers and enumerates UVC devices on the system."""
    
    def __init__(self):
        self.devices: List[UVCDevice] = []
    
    def _get_usb_serial(self, device_path: str) -> Optional[str]:
        """Extract USB serial number from sysfs.
        
        USB video devices have serial numbers accessible via sysfs.
        The path structure is typically:
        /sys/class/video4linux/videoX/device/../.. for USB devices
        
        Args:
            device_path: Device path like /dev/video0
            
        Returns:
            Serial number string or None if not found
        """
        try:
            device_name = os.path.basename(device_path)
            video_sysfs = f"/sys/class/video4linux/{device_name}"
            
            if not os.path.exists(video_sysfs):
                return None
            
            device_link = os.path.join(video_sysfs, "device")
            if not os.path.exists(device_link):
                return None
            
            real_device_path = os.path.realpath(device_link)
            
            serial_paths = [
                os.path.join(real_device_path, "serial"),
                os.path.join(real_device_path, "..", "serial"),
                os.path.join(real_device_path, "..", "..", "serial"),
            ]
            
            for serial_path in serial_paths:
                resolved = os.path.realpath(serial_path)
                if os.path.exists(resolved):
                    with open(resolved, 'r') as f:
                        serial = f.read().strip()
                        if serial and serial != "(null)" and serial != "(error)":
                            return serial
            
            return None
            
        except Exception as e:
            logger.debug(f"Failed to get USB serial for {device_path}: {e}")
            return None
    
    async def discover(self) -> List[UVCDevice]:
        """Discover all UVC devices on the system.
        
        Returns:
            List of UVCDevice objects
        """
        self.devices = []
        
        # Scan /dev/video* devices
        video_devices = await self._scan_video_devices()
        
        for device_path in video_devices:
            try:
                device = await self._get_device_info(device_path)
                if device and device.is_uvc:
                    self.devices.append(device)
                    logger.info(f"Discovered UVC device: {device.name} at {device_path}")
            except Exception as e:
                logger.debug(f"Failed to query {device_path}: {e}")
        
        return self.devices
    
    async def _scan_video_devices(self) -> List[str]:
        """Scan for video device files.
        
        Returns:
            List of device paths like ["/dev/video0", "/dev/video1"]
        """
        devices = []
        
        # Look for /dev/video* nodes
        for i in range(32):  # Check first 32 video devices
            device_path = f"/dev/video{i}"
            if os.path.exists(device_path):
                devices.append(device_path)
        
        return devices
    
    async def _get_device_info(self, device_path: str) -> Optional[UVCDevice]:
        """Get detailed information about a video device.
        
        Args:
            device_path: Path to device (e.g., "/dev/video0")
            
        Returns:
            UVCDevice object or None if not a video capture device
        """
        try:
            # Use v4l2-ctl for safe querying
            cap = await self._query_capabilities(device_path)
            if not cap:
                return None
            
            # Check if it's a capture device with streaming capability
            if not (cap.get("capabilities", 0) & V4L2_CAP_VIDEO_CAPTURE):
                return None
            
            if not (cap.get("capabilities", 0) & V4L2_CAP_STREAMING):
                logger.debug(f"{device_path} does not support streaming")
            
            # Check if it's a UVC device (USB)
            bus_info = cap.get("bus_info", "")
            is_uvc = bus_info.startswith("usb-")
            
            if not is_uvc:
                # Check driver name
                driver = cap.get("driver", "").lower()
                is_uvc = "uvc" in driver or driver == "usb"
            
            # Enumerate supported formats
            formats = await self._enumerate_formats(device_path)
            if not formats:
                logger.debug(f"{device_path} has no usable capture formats")
                return None
            
            # Get USB serial number for persistent device identification
            serial = self._get_usb_serial(device_path)
            
            device = UVCDevice(
                device_path=device_path,
                name=cap.get("card", "Unknown"),
                bus_info=bus_info,
                driver=cap.get("driver", "unknown"),
                is_uvc=is_uvc,
                formats=formats,
                serial=serial
            )
            
            return device
            
        except Exception as e:
            logger.error(f"Error querying {device_path}: {e}")
            return None
    
    async def _query_capabilities(self, device_path: str) -> Optional[Dict]:
        """Query V4L2 device capabilities using v4l2-ctl.
        
        Args:
            device_path: Device path
            
        Returns:
            Dict with capability information
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "v4l2-ctl", "-d", device_path, "--all",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                return None
            
            return self._parse_v4l2_ctl_all(stdout.decode())
            
        except FileNotFoundError:
            logger.warning("v4l2-ctl not found")
            return None
        except Exception as e:
            logger.debug(f"v4l2-ctl query failed: {e}")
            return None
    
    def _parse_v4l2_ctl_all(self, output: str) -> Dict:
        """Parse v4l2-ctl --all output.
        
        Args:
            output: v4l2-ctl --all output
            
        Returns:
            Dict with parsed capabilities
        """
        cap = {
            "driver": "",
            "card": "",
            "bus_info": "",
            "capabilities": 0
        }
        
        for line in output.split("\n"):
            stripped = line.strip()
            if stripped.startswith("Driver name"):
                cap["driver"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Card type"):
                cap["card"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Bus info"):
                cap["bus_info"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Device Caps") or stripped.startswith("Capabilities"):
                value = stripped.split(":", 1)[1].strip().split()[0]
                try:
                    cap["capabilities"] |= int(value, 16)
                except ValueError:
                    logger.debug(f"Failed to parse V4L2 capabilities from line: {stripped}")
        
        return cap
    
    async def _enumerate_formats(self, device_path: str) -> List[VideoFormat]:
        """Enumerate all video formats supported by device.
        
        Args:
            device_path: Device path
            
        Returns:
            List of VideoFormat objects
        """
        formats = []
        
        try:
            proc = await asyncio.create_subprocess_exec(
                "v4l2-ctl", "-d", device_path, "--list-formats-ext",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                formats = self._parse_formats_output(stdout.decode())
                
        except FileNotFoundError:
            logger.warning("v4l2-ctl not found")
        except Exception as e:
            logger.debug(f"Format enumeration failed: {e}")
        
        return formats
    
    def _parse_formats_output(self, output: str) -> List[VideoFormat]:
        """Parse v4l2-ctl --list-formats-ext output.
        
        Args:
            output: v4l2-ctl output
            
        Returns:
            List of VideoFormat objects
        """
        formats = []
        current_format = None
        current_frames = []
        
        for line in output.split("\n"):
            line = line.strip()
            
            if not line:
                continue
            
            # Check for format line: '[0]: 'YUYV' (YUYV 4:2:2)'
            if line.startswith("[") and ":" in line and "'" in line:
                # Save previous format
                if current_format and current_frames:
                    current_format.framesizes = current_frames
                    formats.append(current_format)
                
                # Parse format
                parts = line.split("'")
                if len(parts) >= 2:
                    pixelformat = parts[1]
                    description = parts[2].strip(" ()") if len(parts) > 2 else ""
                    
                    current_format = VideoFormat(
                        pixelformat=pixelformat,
                        description=description,
                        framesizes=[]
                    )
                    current_frames = []
            
            # Parse frame size: 'Size: Discrete 640x480'
            elif line.startswith("Size:") or line.startswith("Interval:"):
                if "Discrete" in line or "Stepwise" in line:
                    # Extract resolution
                    match = self._extract_resolution(line)
                    if match:
                        width, height = match
                        # Default FPS range
                        frame = FrameSize(
                            width=width,
                            height=height,
                            min_fps=1,
                            max_fps=60
                        )
                        
                        # Look for FPS info
                        if "@" in line:
                            fps_match = self._extract_fps(line)
                            if fps_match:
                                frame.max_fps = fps_match
                        
                        current_frames.append(frame)
        
        # Add last format
        if current_format and current_frames:
            current_format.framesizes = current_frames
            formats.append(current_format)
        
        return formats
    
    def _extract_resolution(self, line: str) -> Optional[Tuple[int, int]]:
        """Extract widthxheight from line.
        
        Args:
            line: Input line
            
        Returns:
            Tuple of (width, height) or None
        """
        import re
        match = re.search(r'(\d+)x(\d+)', line)
        if match:
            return (int(match.group(1)), int(match.group(2)))
        return None
    
    def _extract_fps(self, line: str) -> Optional[int]:
        """Extract FPS from line.
        
        Args:
            line: Input line
            
        Returns:
            FPS value or None
        """
        import re
        # Match patterns like "(30.000 fps)" or "@ 1/30"
        match = re.search(r'[( ](\d+(?:\.\d+)?)\s*fps', line)
        if match:
            return int(float(match.group(1)))
        
        match = re.search(r'@\s*1/(\d+)', line)
        if match:
            return int(match.group(1))
        
        return None
    
    def get_devices_json(self) -> str:
        """Get all discovered devices as JSON string."""
        return json.dumps([d.to_dict() for d in self.devices], indent=2)
    
    def get_devices_list(self) -> List[Dict]:
        """Get all discovered devices as list of dicts."""
        return [d.to_dict() for d in self.devices]
    
    def find_device_by_serial(self, serial: str) -> Optional[UVCDevice]:
        """Find a device by its USB serial number.
        
        Args:
            serial: USB serial number to search for
            
        Returns:
            UVCDevice if found, None otherwise
        """
        for device in self.devices:
            if device.serial == serial:
                return device
        return None
    
    def find_device_by_path(self, device_path: str) -> Optional[UVCDevice]:
        """Find a device by its device path.
        
        Args:
            device_path: Device path like /dev/video0
            
        Returns:
            UVCDevice if found, None otherwise
        """
        for device in self.devices:
            if device.device_path == device_path:
                return device
        return None


class UVCPipelineBuilder:
    """Builds GStreamer pipelines for UVC devices."""
    
    def __init__(self, device: UVCDevice):
        self.device = device
    
    def build_pipeline(
        self,
        format_type: str = "auto",
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        encoder: str = "h265",
        bitrate: int = 8000000,
        output_type: str = "srt",
        output_config: Dict = None
    ) -> str:
        """Build a GStreamer pipeline for the UVC device.
        
        Args:
            format_type: "auto", "mjpeg", "yuyv", "h264", or device format
            width: Desired width
            height: Desired height
            fps: Desired framerate
            encoder: Encoder type ("h264", "h265", "none")
            bitrate: Video bitrate
            output_type: Output type ("srt", "rtmp", "file", "display")
            output_config: Output-specific configuration
            
        Returns:
            GStreamer pipeline string
        """
        if output_config is None:
            output_config = {}

        input_format = self._determine_input_format(format_type)
        parts = self._build_video_chain(
            input_format,
            width,
            height,
            fps,
            encoder,
            bitrate,
            output_type,
            output_config,
        )
        return " ! ".join(parts)

    def _build_video_chain(
        self,
        input_format: str,
        width: int,
        height: int,
        fps: int,
        encoder: str,
        bitrate: int,
        output_type: str,
        output_config: Dict,
    ) -> List[str]:
        source_caps = self._build_source_caps(input_format, width, height, fps)

        if input_format == "h264":
            if encoder == "none":
                codec = "h264"
                return [
                    self._build_source(),
                    source_caps,
                    "h264parse config-interval=-1",
                    self._build_output(output_type, output_config, codec),
                ]

            codec = encoder
            return [
                self._build_source(),
                source_caps,
                "h264parse",
                "amlv4l2h264dec",
                "queue max-size-buffers=5 max-size-time=0 max-size-bytes=0",
                'video/x-raw,format=NV12',
                self._build_encoder(codec, bitrate, fps),
                self._build_codec_caps(codec),
                self._build_parser(codec),
                "queue max-size-buffers=30 max-size-time=0 max-size-bytes=0",
                self._build_output(output_type, output_config, codec),
            ]

        if encoder == "none":
            if output_type != "display":
                raise ValueError("Raw UVC display without encoding is only supported for display output")
            parts = [self._build_source(), source_caps]
            decoder = self._build_decoder(input_format)
            if decoder:
                parts.append(decoder)
            converter = self._build_converter(input_format)
            if converter:
                parts.append(converter)
            parts.append("autovideosink sync=false")
            return parts

        codec = encoder
        parts = [self._build_source(), source_caps]
        decoder = self._build_decoder(input_format)
        if decoder:
            parts.append(decoder)
        converter = self._build_converter(input_format)
        if converter:
            parts.append(converter)
        parts.extend([
            'video/x-raw,format=NV12',
            "queue max-size-buffers=5 max-size-time=0 max-size-bytes=0",
            self._build_encoder(codec, bitrate, fps),
            self._build_codec_caps(codec),
            self._build_parser(codec),
            "queue max-size-buffers=30 max-size-time=0 max-size-bytes=0",
            self._build_output(output_type, output_config, codec),
        ])
        return parts
    
    def _determine_input_format(self, format_type: str) -> str:
        """Determine which input format to use.
        
        Args:
            format_type: User-specified format or "auto"
            
        Returns:
            Format type string
        """
        if format_type != "auto":
            return format_type.lower()
        
        # Auto-detect best format
        if self.device.is_h264_passthrough:
            return "h264"
        elif self.device.is_mjpeg:
            return "mjpeg"
        elif self.device.is_yuyv:
            return "yuyv"
        
        # Default to MJPEG if available, otherwise YUYV
        return "mjpeg" if self.device.is_mjpeg else "yuyv"
    
    def _build_source(self) -> str:
        """Build the source element."""
        return f'v4l2src device={self.device.device_path} io-mode=2'

    def _build_source_caps(self, input_format: str, width: int, height: int, fps: int) -> str:
        """Build caps for the selected UVC input format."""
        if input_format == "mjpeg":
            return f'image/jpeg,width={width},height={height},framerate={fps}/1'
        if input_format in ("yuyv", "yuy2"):
            return f'video/x-raw,format=YUY2,width={width},height={height},framerate={fps}/1'
        if input_format == "h264":
            return f'video/x-h264,width={width},height={height},framerate={fps}/1'
        raise ValueError(f"Unsupported UVC input format: {input_format}")
    
    def _build_decoder(self, input_format: str) -> Optional[str]:
        """Build decoder element if needed.
        
        Args:
            input_format: Input format type
            
        Returns:
            Decoder element string or None
        """
        if input_format == "mjpeg":
            return "jpegdec"
        elif input_format in ("yuyv", "yuy2"):
            return None  # videoconvert handles this
        elif input_format == "h264":
            return None  # passthrough, no decoder needed
        return None
    
    def _build_converter(self, input_format: str) -> Optional[str]:
        """Build converter element if needed.
        
        Args:
            input_format: Input format type
            
        Returns:
            Converter element string or None
        """
        if input_format in ("mjpeg", "yuyv", "yuy2"):
            return "videoconvert"
        return None
    
    def _build_encoder(
        self,
        encoder: str,
        bitrate: int,
        fps: int
    ) -> Optional[str]:
        """Build encoder element.
        
        Args:
            encoder: Encoder type ("h264", "h265", "none")
            bitrate: Bitrate in bps
            fps: Framerate
            
        Returns:
            Encoder element string or None
        """
        if encoder == "none":
            return None

        bitrate_kbps = max(1, int(round(bitrate / 1000)))
        gop = 1 if encoder == "h265" else max(5, fps)

        if encoder == "h265":
            return f'amlvenc bitrate={bitrate_kbps} framerate={fps} gop={gop} gop-pattern=0'
        if encoder == "h264":
            return f'amlvenc bitrate={bitrate_kbps} framerate={fps} gop={max(5, fps)} gop-pattern=0'
        raise ValueError(f"Unsupported encoder: {encoder}")

    def _build_codec_caps(self, codec: str) -> str:
        if codec == "h265":
            return 'video/x-h265'
        if codec == "h264":
            return 'video/x-h264'
        raise ValueError(f"Unsupported codec: {codec}")

    def _build_parser(self, codec: str) -> str:
        if codec == "h265":
            return 'h265parse config-interval=-1'
        if codec == "h264":
            return 'h264parse config-interval=-1'
        raise ValueError(f"Unsupported codec: {codec}")

    def _build_srt_uri(self, config: Dict) -> str:
        port = int(config.get("port", 8889))
        mode = config.get("mode", "listener")
        host = config.get("host", "")

        if mode == "caller":
            if not host:
                raise ValueError("SRT caller mode requires a host")
            return f'srt://{host}:{port}'

        return f'srt://:{port}'

    def _build_output(self, output_type: str, config: Dict, codec: str) -> str:
        """Build output element.
        
        Args:
            output_type: Output type
            config: Output configuration
            
        Returns:
            Output element string
        """
        if output_type == "srt":
            uri = self._build_srt_uri(config)
            return (
                f'mpegtsmux alignment=7 latency=100000000 ! '
                f'srtsink uri="{uri}" wait-for-connection=false sync=false'
            )

        if output_type == "rtmp":
            if codec != "h264":
                raise ValueError("RTMP output currently requires H.264 encoding")
            url = config.get("url", "rtmp://localhost/live/stream")
            return f'flvmux streamable=true ! rtmpsink location="{url}"'

        if output_type == "file":
            path = config.get("path", "/mnt/sdcard/uvc_recording.ts")
            return f'mpegtsmux alignment=7 latency=100000000 ! filesink location="{path}"'

        if output_type == "display":
            if codec == "h265":
                return 'avdec_h265 ! videoconvert ! autovideosink sync=false'
            if codec == "h264":
                return 'avdec_h264 ! videoconvert ! autovideosink sync=false'

        return 'fakesink'
    
    def get_supported_formats(self) -> List[Dict]:
        """Get list of supported formats for the device."""
        formats = []
        
        for fmt in self.device.formats:
            formats.append({
                "pixelformat": fmt.pixelformat,
                "description": fmt.description,
                "resolutions": [
                    {"width": fs.width, "height": fs.height, "fps": fs.max_fps}
                    for fs in fmt.framesizes
                ]
            })
        
        return formats


async def discover_uvc_devices() -> List[Dict]:
    """Convenience function to discover UVC devices.
    
    Returns:
        List of device dictionaries
    """
    discovery = UVCDiscovery()
    devices = await discovery.discover()
    return [d.to_dict() for d in devices]


def get_pipeline_for_device(
    device_dict: Dict,
    format_type: str = "auto",
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    encoder: str = "h265",
    bitrate: int = 8000000,
    output_type: str = "srt",
    output_config: Dict = None
) -> str:
    """Generate pipeline for a device from its dictionary representation.
    
    Args:
        device_dict: Device dictionary from UVCDevice.to_dict()
        format_type: Input format type
        width: Width
        height: Height
        fps: Framerate
        encoder: Encoder type
        bitrate: Bitrate
        output_type: Output type
        output_config: Output configuration
        
    Returns:
        GStreamer pipeline string
    """
    device = UVCDevice(
        device_path=device_dict["device_path"],
        name=device_dict["name"],
        bus_info=device_dict["bus_info"],
        driver=device_dict["driver"],
        is_uvc=device_dict["is_uvc"],
        serial=device_dict.get("serial"),
        formats=[
            VideoFormat(
                pixelformat=f["pixelformat"],
                description=f["description"],
                framesizes=[
                    FrameSize(
                        width=fs["width"],
                        height=fs["height"],
                        min_fps=fs["min_fps"],
                        max_fps=fs["max_fps"]
                    )
                    for fs in f["framesizes"]
                ]
            )
            for f in device_dict.get("formats", [])
        ]
    )
    
    builder = UVCPipelineBuilder(device)
    return builder.build_pipeline(
        format_type=format_type,
        width=width,
        height=height,
        fps=fps,
        encoder=encoder,
        bitrate=bitrate,
        output_type=output_type,
        output_config=output_config
    )
