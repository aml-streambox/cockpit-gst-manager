"""UVC Instance Manager - Manages UVC device pipelines with serial ID tracking.

Handles UVC device instances with:
1. Serial ID-based device identification (persistent across reconnections)
2. Auto-start when device is connected
3. Device path resolution from serial number
4. Hot-plug event handling
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable

from instances import InstanceStatus, InstanceType

logger = logging.getLogger("gst-manager.uvc_instance")


@dataclass
class UVCInstanceConfig:
    """Configuration for a UVC instance."""
    device_serial: Optional[str] = None
    device_path: Optional[str] = None
    device_name: Optional[str] = None  
    format_type: str = "auto"
    width: int = 1920
    height: int = 1080
    fps: int = 30
    encoder: str = "h265"
    bitrate: int = 8000000
    output_type: str = "srt"
    output_config: Dict[str, Any] = None
    autostart: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UVCInstanceConfig":
        valid_fields = cls.__dataclass_fields__.keys()
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)


class UVCInstanceManager:
    """Manages UVC device instances with serial ID tracking.
    
    Key features:
    - Stores device serial ID for persistent identification
    - Resolves device path from serial on startup/reconnection
    - Auto-starts instances when device becomes available
    - Monitors for UVC device hot-plug events
    """
    
    CONFIG_DIR = Path("/var/lib/gst-manager/uvc_instances")
    
    def __init__(self, instance_manager, event_manager=None):
        """Initialize UVC instance manager.
        
        Args:
            instance_manager: InstanceManager for creating pipelines
            event_manager: EventManager for device event callbacks
        """
        self.instance_manager = instance_manager
        self.event_manager = event_manager
        self.uvc_configs: Dict[str, UVCInstanceConfig] = {}  # instance_id -> config
        self._device_monitor_task: Optional[asyncio.Task] = None
        self._last_known_devices: Dict[str, str] = {}  # serial -> device_path
        self._running = False
        self._device_callbacks: List[Callable] = []
        
    async def load(self) -> None:
        """Load saved UVC instance configurations."""
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        
        if not self.CONFIG_DIR.exists():
            return
        
        for config_file in self.CONFIG_DIR.glob("*.json"):
            try:
                with open(config_file, "r") as f:
                    data = json.load(f)
                
                instance_id = data.get("instance_id")
                if not instance_id:
                    continue
                
                config = UVCInstanceConfig.from_dict(data.get("config", {}))
                self.uvc_configs[instance_id] = config
                logger.info(f"Loaded UVC config for instance {instance_id}: serial={config.device_serial}")
                
            except Exception as e:
                logger.error(f"Failed to load UVC config {config_file}: {e}")
    
    async def save_config(self, instance_id: str) -> None:
        """Save UVC instance configuration to disk."""
        config = self.uvc_configs.get(instance_id)
        if not config:
            return
        
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config_file = self.CONFIG_DIR / f"{instance_id}.json"
        
        try:
            data = {
                "instance_id": instance_id,
                "config": config.to_dict()
            }
            with open(config_file, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved UVC config for {instance_id}")
        except Exception as e:
            logger.error(f"Failed to save UVC config: {e}")
    
    def delete_config(self, instance_id: str) -> None:
        """Delete UVC instance configuration file."""
        config_file = self.CONFIG_DIR / f"{instance_id}.json"
        try:
            if config_file.exists():
                config_file.unlink()
                logger.debug(f"Deleted UVC config for {instance_id}")
        except Exception as e:
            logger.error(f"Failed to delete UVC config: {e}")
        
        if instance_id in self.uvc_configs:
            del self.uvc_configs[instance_id]
    
    async def create_instance(
        self,
        name: str,
        device_serial: Optional[str],
        device_path: str,
        device_name: Optional[str],
        format_type: str = "auto",
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        encoder: str = "h265",
        bitrate: int = 8000000,
        output_type: str = "srt",
        output_config: Dict[str, Any] = None,
        autostart: bool = False
    ) -> str:
        """Create a new UVC instance with serial ID tracking.
        
        Args:
            name: Instance name
            device_serial: USB serial number for persistent identification
            device_path: Current device path (e.g., "/dev/video0")
            device_name: Human-readable device name
            format_type: Input format ("auto", "mjpeg", "yuyv", "h264")
            width: Video width
            height: Video height
            fps: Framerate
            encoder: Encoder type ("h264", "h265", "none")
            bitrate: Video bitrate in bps
            output_type: Output type ("srt", "rtmp", "file", "display")
            output_config: Output-specific configuration
            autostart: Auto-start when device is connected
            
        Returns:
            Instance ID
        """
        from uvc_utils import UVCDiscovery, UVCPipelineBuilder
        
        # Discover device to get full info
        discovery = UVCDiscovery()
        devices = await discovery.discover()
        
        device = None
        if device_serial:
            # Find by serial first (preferred for persistence)
            device = discovery.find_device_by_serial(device_serial)
        
        if not device:
            # Fallback to path
            device = discovery.find_device_by_path(device_path)
        
        if not device:
            raise ValueError(f"Device not found: serial={device_serial}, path={device_path}")
        
        # Build pipeline
        if output_config is None:
            output_config = {}
        
        builder = UVCPipelineBuilder(device)
        pipeline = builder.build_pipeline(
            format_type=format_type,
            width=width,
            height=height,
            fps=fps,
            encoder=encoder,
            bitrate=bitrate,
            output_type=output_type,
            output_config=output_config
        )
        
        # Create instance
        instance_id = await self.instance_manager.create_instance(name, pipeline)
        
        # Store UVC config with serial
        config = UVCInstanceConfig(
            device_serial=device.serial,
            device_path=device.device_path,
            device_name=device.name,
            format_type=format_type,
            width=width,
            height=height,
            fps=fps,
            encoder=encoder,
            bitrate=bitrate,
            output_type=output_type,
            output_config=output_config,
            autostart=autostart
        )
        
        self.uvc_configs[instance_id] = config
        
        # Update instance type
        instance = self.instance_manager.get_instance(instance_id)
        if instance:
            instance.instance_type = InstanceType.UVC
            instance.uvc_config = config.to_dict()
            if autostart:
                instance.autostart = True
                instance.trigger_event = "uvc_device_ready"
            await self.instance_manager.history_manager.save_instance(instance.to_dict())
        
        await self.save_config(instance_id)
        logger.info(f"Created UVC instance {instance_id}: serial={device.serial}, path={device.device_path}")
        
        return instance_id
    
    async def update_instance(
        self,
        instance_id: str,
        name: Optional[str] = None,
        device_serial: Optional[str] = None,
        device_path: Optional[str] = None,
        format_type: Optional[str] = None,
        width: Optional[int] =None,
        height: Optional[int] = None,
        fps: Optional[int] = None,
        encoder: Optional[str] = None,
        bitrate: Optional[int] = None,
        output_type: Optional[str] = None,
        output_config: Optional[Dict[str, Any]] = None,
        autostart: Optional[bool] = None
    ) -> bool:
        """Update an existing UVC instance.
        
        Args:
            instance_id: Instance ID to update
            ... (other args same as create)
            
        Returns:
            True if successful
        """
        from uvc_utils import UVCDiscovery, UVCPipelineBuilder
        
        config = self.uvc_configs.get(instance_id)
        if not config:
            logger.error(f"No UVC config found for instance {instance_id}")
            return False
        
        instance = self.instance_manager.get_instance(instance_id)
        if not instance:
            logger.error(f"Instance not found: {instance_id}")
            return False
        
        # Stop instance if running
        if instance.status == InstanceStatus.RUNNING:
            await self.instance_manager.stop_instance(instance_id)
        
        # Update config values
        if device_serial is not None:
            config.device_serial = device_serial
        if device_path is not None:
            config.device_path = device_path
        if name is not None:
            instance.name = name
        if format_type is not None:
            config.format_type = format_type
        if width is not None:
            config.width = width
        if height is not None:
            config.height = height
        if fps is not None:
            config.fps = fps
        if encoder is not None:
            config.encoder = encoder
        if bitrate is not None:
            config.bitrate = bitrate
        if output_type is not None:
            config.output_type = output_type
        if output_config is not None:
            config.output_config = output_config
        if autostart is not None:
            config.autostart = autostart
            instance.autostart = autostart
            instance.trigger_event = "uvc_device_ready" if autostart else None
        
        # Resolve device path from serial if needed
        discovery = UVCDiscovery()
        await discovery.discover()
        
        resolved_device = None
        if config.device_serial:
            resolved_device = discovery.find_device_by_serial(config.device_serial)
        
        if not resolved_device and config.device_path:
            resolved_device = discovery.find_device_by_path(config.device_path)
        
        if not resolved_device:
            logger.error(f"Device not found for instance {instance_id}: serial={config.device_serial}")
            return False
        
        # Update device path from resolved device
        config.device_path = resolved_device.device_path
        config.device_name = resolved_device.name
        
        # Rebuild pipeline
        builder = UVCPipelineBuilder(resolved_device)
        pipeline = builder.build_pipeline(
            format_type=config.format_type,
            width=config.width,
            height=config.height,
            fps=config.fps,
            encoder=config.encoder,
            bitrate=config.bitrate,
            output_type=config.output_type,
            output_config=config.output_config or {}
        )
        
        instance.pipeline = pipeline
        instance.uvc_config = config.to_dict()
        
        await self.instance_manager.history_manager.save_instance(instance.to_dict())
        await self.save_config(instance_id)
        
        logger.info(f"Updated UVC instance {instance_id}")
        return True
    
    async def resolve_device_path(self, instance_id: str) -> Optional[str]:
        """Resolve current device path from serial number.
        
        Args:
            instance_id: Instance ID
            
        Returns:
            Device path if found, None otherwise
        """
        config = self.uvc_configs.get(instance_id)
        if not config:
            return None
        
        if not config.device_serial:
            return config.device_path
        
        from uvc_utils import UVCDiscovery
        
        discovery = UVCDiscovery()
        await discovery.discover()
        
        device = discovery.find_device_by_serial(config.device_serial)
        if device:
            # Update cached path
            config.device_path = device.device_path
            return device.device_path
        
        return None
    
    async def on_devices_changed(self, devices: List[Any]) -> None:
        """Handle UVC device changes (hot-plug events).
        
        Called by EventManager when UVC devices are enumerated.
        
        Args:
            devices: List of discovered UVCDevice objects
        """
        # Build serial -> path mapping from current devices
        current_devices: Dict[str, str] = {}
        for device in devices:
            if device.serial:
                current_devices[device.serial] = device.device_path
        
        # Check for new devices
        added_serials = set(current_devices.keys()) - set(self._last_known_devices.keys())
        
        # Check for removed devices
        removed_serials = set(self._last_known_devices.keys()) - set(current_devices.keys())
        
        # Update last known devices
        self._last_known_devices = current_devices
        
        # Handle device events
        for serial in added_serials:
            device_path = current_devices[serial]
            logger.info(f"UVC device connected: serial={serial}, path={device_path}")
            await self._on_device_connected(serial, device_path)
        
        for serial in removed_serials:
            logger.info(f"UVC device disconnected: serial={serial}")
            await self._on_device_disconnected(serial)
    
    async def _on_device_connected(self, serial: str, device_path: str) -> None:
        """Handle UVC device connection.
        
        Args:
            serial: USB serial number
            device_path: Device path (e.g., /dev/video0)
        """
        # Find all instances configured for this device
        for instance_id, config in list(self.uvc_configs.items()):
            if config.device_serial == serial:
                # Update device path
                config.device_path = device_path
                
                instance = self.instance_manager.get_instance(instance_id)
                if not instance:
                    continue
                
                # Auto-start if configured
                if config.autostart and instance.status == InstanceStatus.STOPPED:
                    logger.info(f"Auto-starting UVC instance {instance_id} for device {serial}")
                    try:
                        await self._restart_instance_with_device(instance_id, device_path)
                    except Exception as e:
                        logger.error(f"Failed to auto-start UVC instance {instance_id}: {e}")
    
    async def _on_device_disconnected(self, serial: str) -> None:
        """Handle UVC device disconnection.
        
        Args:
            serial: USB serial number
        """
        # Find all instances using this device
        for instance_id, config in list(self.uvc_configs.items()):
            if config.device_serial == serial:
                instance = self.instance_manager.get_instance(instance_id)
                if not instance:
                    continue
                
                # Stop if running
                if instance.status == InstanceStatus.RUNNING:
                    logger.info(f"Stopping UVC instance {instance_id} due to device disconnect")
                    try:
                        await self.instance_manager.stop_instance(instance_id)
                        # Set status to waiting_signal for auto-restart
                        await self.instance_manager.set_instance_status(
                            instance_id,
                            InstanceStatus.WAITING_SIGNAL,
                            error_message="device disconnected"
                        )
                    except Exception as e:
                        logger.error(f"Failed to stop UVC instance {instance_id}: {e}")
    
    async def _restart_instance_with_device(self, instance_id: str, device_path: str) -> None:
        """Restart an instance with updated device path.
        
        Args:
            instance_id: Instance ID
            device_path: New device path
        """
        from uvc_utils import UVCDiscovery, UVCPipelineBuilder
        
        config = self.uvc_configs.get(instance_id)
        if not config:
            return
        
        instance = self.instance_manager.get_instance(instance_id)
        if not instance:
            return
        
        # Find device
        discovery = UVCDiscovery()
        await discovery.discover()
        device = discovery.find_device_by_path(device_path)
        
        if not device:
            logger.error(f"Device not found at {device_path}")
            return
        
        # Rebuild pipeline with new device path
        builder = UVCPipelineBuilder(device)
        pipeline = builder.build_pipeline(
            format_type=config.format_type,
            width=config.width,
            height=config.height,
            fps=config.fps,
            encoder=config.encoder,
            bitrate=config.bitrate,
            output_type=config.output_type,
            output_config=config.output_config or {}
        )
        
        # Update instance
        instance.pipeline = pipeline
        config.device_path = device_path
        instance.uvc_config = config.to_dict()
        
        # Save updated config
        await self.instance_manager.history_manager.save_instance(instance.to_dict())
        await self.save_config(instance_id)
        
        # Start instance
        await self.instance_manager.start_instance(instance_id)
    
    async def start_all_autostart(self) -> None:
        """Start all UVC instances configured for auto-start.
        
        Called during system boot to start UVC instances
        if their device is currently connected.
        """
        from uvc_utils import UVCDiscovery
        
        # Discover current devices
        discovery = UVCDiscovery()
        devices = await discovery.discover()
        
        # Build serial -> device mapping
        serial_to_device: Dict[str, str] = {}
        for device in devices:
            if device.serial:
                serial_to_device[device.serial] = device.device_path
        
        # Start auto-start instances
        for instance_id, config in list(self.uvc_configs.items()):
            if not config.autostart:
                continue
            
            instance = self.instance_manager.get_instance(instance_id)
            if not instance:
                continue
            
            if instance.status != InstanceStatus.STOPPED:
                continue
            
            # Check if device is available
            if config.device_serial and config.device_serial in serial_to_device:
                device_path = serial_to_device[config.device_serial]
                logger.info(f"Auto-starting UVC instance {instance_id} (serial={config.device_serial})")
                try:
                    await self._restart_instance_with_device(instance_id, device_path)
                except Exception as e:
                    logger.error(f"Failed to auto-start UVC instance {instance_id}: {e}")
            elif config.device_serial:
                logger.info(
                    f"UVC instance {instance_id} device not connected (serial={config.device_serial}), "
                    f"will start when device is plugged in"
                )
                # Mark as waiting for device
                await self.instance_manager.set_instance_status(
                    instance_id,
                    InstanceStatus.WAITING_SIGNAL,
                    error_message="waiting for device"
                )
    
    def get_config(self, instance_id: str) -> Optional[UVCInstanceConfig]:
        """Get UVC config for an instance.
        
        Args:
            instance_id: Instance ID
            
        Returns:
            UVCInstanceConfig or None
        """
        return self.uvc_configs.get(instance_id)
    
    def get_instance_for_serial(self, serial: str) -> Optional[str]:
        """Get instance ID for a device serial.
        
        Args:
            serial: USB device serial
            
        Returns:
            Instance ID if found, None otherwise
        """
        for instance_id, config in self.uvc_configs.items():
            if config.device_serial == serial:
                return instance_id
        return None