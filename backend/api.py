"""D-Bus API Interface for GStreamer Manager.

Implements the org.cockpit.GstManager1 interface.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, List

# Try to import dbus-next (preferred) or fallback to dbus-python
try:
    from dbus_next.aio import MessageBus
    from dbus_next.service import ServiceInterface, method, signal, dbus_property
    from dbus_next import Variant, DBusError, BusType
    DBUS_LIBRARY = "dbus_next"
except ImportError:
    # Fallback to dbus-python
    import dbus
    import dbus.service
    import dbus.mainloop.glib
    from gi.repository import GLib
    DBUS_LIBRARY = "dbus_python"

logger = logging.getLogger("gst-manager.api")


# D-Bus service configuration
SERVICE_NAME = "org.cockpit.GstManager"
OBJECT_PATH = "/org/cockpit/GstManager"
INTERFACE_NAME = "org.cockpit.GstManager1"


class GstManagerError(Exception):
    """Base exception for GstManager errors."""
    pass


class InstanceNotFoundError(GstManagerError):
    """Instance ID not found."""
    pass


class InstanceRunningError(GstManagerError):
    """Cannot modify running instance."""
    pass


class InvalidConfigError(GstManagerError):
    """Invalid configuration provided."""
    pass


if DBUS_LIBRARY == "dbus_next":
    # dbus-next implementation (preferred for asyncio)

    class GstManagerInterface(ServiceInterface):
        """D-Bus interface implementation using dbus-next."""

        def __init__(
            self,
            instance_manager,
            discovery_manager,
            history_manager,
            config: Dict
        ):
            super().__init__(INTERFACE_NAME)
            self.instance_manager = instance_manager
            self.discovery_manager = discovery_manager
            self.history_manager = history_manager
            self.config = config
            self.event_manager = None  # Set after EventManager is created
            self.ai_agent = None  # Set after AI agent is created

            # Register for status change callbacks
            self.instance_manager.add_status_callback(self._on_status_change)

        async def _save_config(self) -> None:
            """Save config to disk."""
            config_path = Path("/var/lib/gst-manager/config.json")
            try:
                import aiofiles
                async with aiofiles.open(config_path, "w") as f:
                    await f.write(json.dumps(self.config, indent=2))
                logger.debug(f"Saved config to {config_path}")
            except ImportError:
                # Fallback to sync write
                with open(config_path, "w") as f:
                    json.dump(self.config, f, indent=2)
                logger.debug(f"Saved config to {config_path} (sync)")
            except Exception as e:
                logger.error(f"Failed to save config: {e}")

        async def _on_status_change(self, instance_id: str, status: str) -> None:
            """Callback for instance status changes."""
            self.InstanceStatusChanged(instance_id, status)

        # --- Instance Management Methods ---

        @method()
        def ListInstances(self) -> "s":
            """Get all configured instances as JSON."""
            instances = self.instance_manager.list_instances()
            return json.dumps(instances)

        @method()
        async def CreateInstance(self, name: "s", pipeline: "s") -> "s":
            """Create a new pipeline instance."""
            try:
                instance_id = await self.instance_manager.create_instance(name, pipeline)
                return instance_id
            except Exception as e:
                logger.error(f"CreateInstance failed: {e}")
                raise DBusError(f"{INTERFACE_NAME}.InvalidConfig", str(e))

        @method()
        async def DeleteInstance(self, instance_id: "s") -> "b":
            """Delete an instance (must be stopped)."""
            try:
                return await self.instance_manager.delete_instance(instance_id)
            except ValueError as e:
                if "not found" in str(e).lower():
                    raise DBusError(f"{INTERFACE_NAME}.InstanceNotFound", str(e))
                elif "running" in str(e).lower():
                    raise DBusError(f"{INTERFACE_NAME}.InstanceRunning", str(e))
                raise DBusError(f"{INTERFACE_NAME}.Error", str(e))

        @method()
        async def StartInstance(self, instance_id: "s") -> "b":
            """Start a pipeline instance."""
            try:
                return await self.instance_manager.start_instance(instance_id)
            except ValueError as e:
                raise DBusError(f"{INTERFACE_NAME}.InstanceNotFound", str(e))

        @method()
        async def StopInstance(self, instance_id: "s") -> "b":
            """Stop a running pipeline instance."""
            try:
                return await self.instance_manager.stop_instance(instance_id)
            except ValueError as e:
                raise DBusError(f"{INTERFACE_NAME}.InstanceNotFound", str(e))

        @method()
        def GetInstanceStatus(self, instance_id: "s") -> "s":
            """Get detailed status of an instance as JSON."""
            try:
                status = self.instance_manager.get_instance_status(instance_id)
                return json.dumps(status)
            except ValueError as e:
                raise DBusError(f"{INTERFACE_NAME}.InstanceNotFound", str(e))

        @method()
        async def UpdatePipeline(self, instance_id: "s", pipeline: "s") -> "b":
            """Update pipeline CLI (instance must be stopped)."""
            try:
                return await self.instance_manager.update_pipeline(instance_id, pipeline)
            except ValueError as e:
                if "not found" in str(e).lower():
                    raise DBusError(f"{INTERFACE_NAME}.InstanceNotFound", str(e))
                elif "running" in str(e).lower():
                    raise DBusError(f"{INTERFACE_NAME}.InstanceRunning", str(e))
                raise DBusError(f"{INTERFACE_NAME}.Error", str(e))

        # --- Dynamic Control Methods ---

        @method()
        def GetInstanceLogs(self, instance_id: "s", lines: "i") -> "s":
            """Get error logs for an instance as JSON array."""
            try:
                logs = self.instance_manager.get_instance_logs(
                    instance_id, lines if lines > 0 else 50
                )
                return json.dumps(logs)
            except ValueError as e:
                raise DBusError(f"{INTERFACE_NAME}.InstanceNotFound", str(e))

        @method()
        def ClearInstanceLogs(self, instance_id: "s") -> "b":
            """Clear error logs for an instance."""
            try:
                return self.instance_manager.clear_instance_logs(instance_id)
            except ValueError as e:
                raise DBusError(f"{INTERFACE_NAME}.InstanceNotFound", str(e))

        # --- Discovery Methods ---

        @method()
        def GetBoardContext(self) -> "s":
            """Get current hardware discovery information as JSON."""
            return self.discovery_manager.get_context_json()

        # --- HDMI & Event Methods ---

        @method()
        def GetHdmiStatus(self) -> "s":
            """Get current HDMI input status as JSON."""
            if self.event_manager:
                import json
                return json.dumps(self.event_manager.get_hdmi_status())
            return json.dumps({"available": False, "error": "Event manager not initialized"})

        @method()
        async def SetInstanceAutostart(
            self,
            instance_id: "s",
            enabled: "b",
            trigger_event: "s"
        ) -> "b":
            """Configure autostart for an instance.
            
            Args:
                instance_id: Instance ID
                enabled: Enable/disable autostart
                trigger_event: Event trigger (e.g., 'hdmi_signal_ready', 'boot', '')
            """
            try:
                instance = self.instance_manager.get_instance(instance_id)
                if not instance:
                    raise DBusError(f"{INTERFACE_NAME}.InstanceNotFound", instance_id)
                
                instance.autostart = enabled
                instance.trigger_event = trigger_event if trigger_event else None
                
                # Save to disk
                await self.history_manager.save_instance(instance.to_dict())
                logger.info(f"Set autostart for {instance_id}: enabled={enabled}, trigger={trigger_event}")
                return True
            except Exception as e:
                logger.error(f"SetInstanceAutostart failed: {e}")
                return False

        # --- AI Methods (Phase 4) ---

        @method()
        async def AiGeneratePipeline(self, prompt: "s", provider: "s") -> "s":
            """Generate a pipeline from natural language prompt."""
            if not self.ai_agent:
                return json.dumps({"error": "AI not initialized"})
            
            result = await self.ai_agent.generate_pipeline(
                prompt,
                provider if provider else None
            )
            return json.dumps(result)

        @method()
        async def AiFixError(self, pipeline: "s", error: "s") -> "s":
            """Analyze error and suggest fix."""
            if not self.ai_agent:
                return json.dumps({"error": "AI not initialized"})
            
            result = await self.ai_agent.fix_error(pipeline, error)
            return json.dumps(result)

        @method()
        def GetAiProviders(self) -> "s":
            """Get configured AI providers as JSON."""
            providers = self.config.get("ai_providers", [])
            # Remove API keys from response
            safe_providers = []
            for p in providers:
                safe = {k: v for k, v in p.items() if k != "api_key"}
                safe["has_key"] = bool(p.get("api_key"))
                safe_providers.append(safe)
            return json.dumps(safe_providers)

        @method()
        async def AddAiProvider(
            self,
            name: "s",
            url: "s",
            api_key: "s",
            model: "s"
        ) -> "b":
            """Add or update an AI provider."""
            try:
                providers = self.config.setdefault("ai_providers", [])
                
                # Check if provider exists (update case)
                existing_idx = None
                for i, p in enumerate(providers):
                    if p.get("name") == name:
                        existing_idx = i
                        break
                
                # Handle __KEEP__ marker for preserving existing key
                actual_key = api_key
                if api_key == "__KEEP__" and existing_idx is not None:
                    actual_key = providers[existing_idx].get("api_key", "")
                
                new_provider = {
                    "name": name,
                    "url": url,
                    "api_key": actual_key,
                    "model": model
                }
                
                if existing_idx is not None:
                    # Replace existing
                    providers[existing_idx] = new_provider
                    logger.info(f"Updated AI provider: {name}")
                else:
                    # Add new
                    providers.append(new_provider)
                    logger.info(f"Added AI provider: {name}")
                
                # Save config to disk
                await self._save_config()
                
                # Reload AI agent if exists
                if self.ai_agent:
                    self.ai_agent.provider_manager.remove_provider(name)
                    self.ai_agent.provider_manager.add_provider(name, url, actual_key, model)
                
                return True
            except Exception as e:
                logger.error(f"AddAiProvider failed: {e}")
                return False

        @method()
        async def RemoveAiProvider(self, name: "s") -> "b":
            """Remove an AI provider."""
            try:
                providers = self.config.get("ai_providers", [])
                self.config["ai_providers"] = [p for p in providers if p.get("name") != name]
                
                # Save config to disk
                await self._save_config()
                
                if self.ai_agent:
                    self.ai_agent.provider_manager.remove_provider(name)
                
                logger.info(f"Removed AI provider: {name}")
                return True
            except Exception as e:
                logger.error(f"RemoveAiProvider failed: {e}")
                return False

        # --- Import/Export Methods ---

        @method()
        async def ExportInstance(self, instance_id: "s") -> "s":
            """Export instance configuration as JSON."""
            result = await self.history_manager.export_instance(instance_id)
            if result is None:
                raise DBusError(f"{INTERFACE_NAME}.InstanceNotFound", instance_id)
            return result

        @method()
        async def ImportInstance(self, config_json: "s") -> "s":
            """Import instance configuration from JSON."""
            try:
                instance_id = await self.history_manager.import_instance(config_json)
                if instance_id:
                    # Reload into instance manager
                    instances = await self.history_manager.load_all_instances()
                    for inst_data in instances:
                        if inst_data.get("id") == instance_id:
                            from instances import Instance
                            instance = Instance.from_dict(inst_data)
                            self.instance_manager.instances[instance_id] = instance
                            break
                    return instance_id
                raise DBusError(f"{INTERFACE_NAME}.InvalidConfig", "Import failed")
            except json.JSONDecodeError as e:
                raise DBusError(f"{INTERFACE_NAME}.InvalidConfig", str(e))

        # --- Signals ---

        @signal()
        def InstanceStatusChanged(self, instance_id: "s", status: "s") -> "ss":
            """Emitted when an instance status changes."""
            return [instance_id, status]

        @signal()
        def HdmiSignalChanged(self, available: "b", resolution: "s") -> "bs":
            """Emitted when HDMI input signal changes."""
            return [available, resolution]


    class GstManagerService:
        """D-Bus service manager using dbus-next."""

        def __init__(
            self,
            instance_manager,
            discovery_manager,
            history_manager,
            config: Dict
        ):
            self.instance_manager = instance_manager
            self.discovery_manager = discovery_manager
            self.history_manager = history_manager
            self.config = config
            self.bus = None
            self.interface = None

        async def start(self) -> None:
            """Start the D-Bus service."""
            self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

            self.interface = GstManagerInterface(
                self.instance_manager,
                self.discovery_manager,
                self.history_manager,
                self.config
            )

            self.bus.export(OBJECT_PATH, self.interface)
            await self.bus.request_name(SERVICE_NAME)

            logger.info(f"D-Bus service started: {SERVICE_NAME}")

        async def stop(self) -> None:
            """Stop the D-Bus service."""
            if self.bus:
                self.bus.disconnect()
                logger.info("D-Bus service stopped")

        def emit_hdmi_signal(self, available: bool, resolution: str) -> None:
            """Emit HDMI signal changed signal."""
            if self.interface:
                self.interface.HdmiSignalChanged(available, resolution)


else:
    # dbus-python fallback implementation

    class GstManagerInterface(dbus.service.Object):
        """D-Bus interface implementation using dbus-python."""

        def __init__(
            self,
            bus_name,
            instance_manager,
            discovery_manager,
            history_manager,
            config: Dict
        ):
            super().__init__(bus_name, OBJECT_PATH)
            self.instance_manager = instance_manager
            self.discovery_manager = discovery_manager
            self.history_manager = history_manager
            self.config = config

        @dbus.service.method(INTERFACE_NAME, in_signature="", out_signature="s")
        def ListInstances(self) -> str:
            """Get all configured instances as JSON."""
            instances = self.instance_manager.list_instances()
            return json.dumps(instances)

        @dbus.service.method(INTERFACE_NAME, in_signature="ss", out_signature="s")
        def CreateInstance(self, name: str, pipeline: str) -> str:
            """Create a new pipeline instance."""
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(
                self.instance_manager.create_instance(name, pipeline)
            )

        @dbus.service.method(INTERFACE_NAME, in_signature="s", out_signature="b")
        def DeleteInstance(self, instance_id: str) -> bool:
            """Delete an instance."""
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(
                self.instance_manager.delete_instance(instance_id)
            )

        @dbus.service.method(INTERFACE_NAME, in_signature="s", out_signature="b")
        def StartInstance(self, instance_id: str) -> bool:
            """Start a pipeline instance."""
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(
                self.instance_manager.start_instance(instance_id)
            )

        @dbus.service.method(INTERFACE_NAME, in_signature="s", out_signature="b")
        def StopInstance(self, instance_id: str) -> bool:
            """Stop a running pipeline instance."""
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(
                self.instance_manager.stop_instance(instance_id)
            )

        @dbus.service.method(INTERFACE_NAME, in_signature="s", out_signature="s")
        def GetInstanceStatus(self, instance_id: str) -> str:
            """Get detailed status as JSON."""
            status = self.instance_manager.get_instance_status(instance_id)
            return json.dumps(status)

        @dbus.service.method(INTERFACE_NAME, in_signature="ss", out_signature="b")
        def UpdatePipeline(self, instance_id: str, pipeline: str) -> bool:
            """Update pipeline CLI."""
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(
                self.instance_manager.update_pipeline(instance_id, pipeline)
            )

        @dbus.service.method(INTERFACE_NAME, in_signature="", out_signature="s")
        def GetBoardContext(self) -> str:
            """Get hardware discovery as JSON."""
            return self.discovery_manager.get_context_json()

        @dbus.service.method(INTERFACE_NAME, in_signature="s", out_signature="s")
        def ExportInstance(self, instance_id: str) -> str:
            """Export instance configuration."""
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(
                self.history_manager.export_instance(instance_id)
            ) or ""

        @dbus.service.method(INTERFACE_NAME, in_signature="s", out_signature="s")
        def ImportInstance(self, config_json: str) -> str:
            """Import instance configuration."""
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(
                self.history_manager.import_instance(config_json)
            ) or ""

        @dbus.service.signal(INTERFACE_NAME, signature="ss")
        def InstanceStatusChanged(self, instance_id: str, status: str):
            """Emitted when instance status changes."""
            pass

        @dbus.service.signal(INTERFACE_NAME, signature="bs")
        def HdmiSignalChanged(self, available: bool, resolution: str):
            """Emitted when HDMI signal changes."""
            pass


    class GstManagerService:
        """D-Bus service manager using dbus-python."""

        def __init__(
            self,
            instance_manager,
            discovery_manager,
            history_manager,
            config: Dict
        ):
            self.instance_manager = instance_manager
            self.discovery_manager = discovery_manager
            self.history_manager = history_manager
            self.config = config
            self.interface = None
            self.mainloop = None

        async def start(self) -> None:
            """Start the D-Bus service."""
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            bus = dbus.SystemBus()
            bus_name = dbus.service.BusName(SERVICE_NAME, bus)

            self.interface = GstManagerInterface(
                bus_name,
                self.instance_manager,
                self.discovery_manager,
                self.history_manager,
                self.config
            )

            logger.info(f"D-Bus service started: {SERVICE_NAME}")

        async def stop(self) -> None:
            """Stop the D-Bus service."""
            logger.info("D-Bus service stopped")

        def emit_hdmi_signal(self, available: bool, resolution: str) -> None:
            """Emit HDMI signal changed signal."""
            if self.interface:
                self.interface.HdmiSignalChanged(available, resolution)
