"""Instance Manager - GStreamer pipeline process management.

Handles creation, lifecycle, and monitoring of GStreamer pipeline instances.
"""

import asyncio
import logging
import uuid
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List, Callable, Any

logger = logging.getLogger("gst-manager.instances")


class InstanceStatus(Enum):
    """Pipeline instance status."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"
    WAITING_SIGNAL = "waiting_signal"


@dataclass
class RecoveryConfig:
    """Recovery configuration for an instance."""
    auto_restart: bool = True
    max_retries: int = 3
    retry_delay_seconds: int = 5
    restart_on_signal: bool = True


@dataclass
class Instance:
    """GStreamer pipeline instance."""
    id: str
    name: str
    pipeline: str
    status: InstanceStatus = InstanceStatus.STOPPED
    pid: Optional[int] = None
    autostart: bool = False
    trigger_event: Optional[str] = None
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)
    created_at: str = ""
    modified_at: str = ""
    error_message: Optional[str] = None
    retry_count: int = 0
    uptime_start: Optional[float] = None
    error_logs: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Instance":
        """Create instance from dictionary."""
        # Handle nested configs
        if "recovery" in data and isinstance(data["recovery"], dict):
            data["recovery"] = RecoveryConfig(**data["recovery"])
        # Handle status enum
        if "status" in data and isinstance(data["status"], str):
            data["status"] = InstanceStatus(data["status"])
        return cls(**data)


# Error patterns for transient vs fatal classification
TRANSIENT_ERRORS = [
    "connection refused",
    "connection reset",
    "timeout",
    "buffer underrun",
    "temporary failure",
    "resource temporarily unavailable",
]

FATAL_ERRORS = [
    "device not found",
    "no such file",
    "permission denied",
    "no element",
    "invalid pipeline",
    "encoder failure",
]


class InstanceManager:
    """Manages GStreamer pipeline instances."""

    def __init__(self, history_manager):
        self.history_manager = history_manager
        self.instances: Dict[str, Instance] = {}
        self.processes: Dict[str, asyncio.subprocess.Process] = {}
        self.status_callbacks: List[Callable] = []

    async def load_instances(self) -> None:
        """Load saved instances from history."""
        saved = await self.history_manager.load_all_instances()
        for instance_data in saved:
            try:
                instance = Instance.from_dict(instance_data)
                # Reset runtime state
                instance.status = InstanceStatus.STOPPED
                instance.pid = None
                instance.error_message = None
                instance.retry_count = 0
                self.instances[instance.id] = instance
                logger.info(f"Loaded instance: {instance.id} ({instance.name})")
            except Exception as e:
                logger.error(f"Failed to load instance: {e}")

    def add_status_callback(self, callback: Callable) -> None:
        """Register callback for status changes."""
        self.status_callbacks.append(callback)

    async def _notify_status_change(self, instance_id: str, status: str) -> None:
        """Notify all callbacks of status change."""
        for callback in self.status_callbacks:
            try:
                await callback(instance_id, status)
            except Exception as e:
                logger.error(f"Status callback error: {e}")

    def list_instances(self) -> List[dict]:
        """Get all instances as dictionaries."""
        return [inst.to_dict() for inst in self.instances.values()]

    def get_instance(self, instance_id: str) -> Optional[Instance]:
        """Get instance by ID."""
        return self.instances.get(instance_id)

    async def create_instance(self, name: str, pipeline: str) -> str:
        """Create a new pipeline instance.

        Args:
            name: Display name for the instance.
            pipeline: GStreamer CLI pipeline string.

        Returns:
            str: Instance ID.
        """
        instance_id = str(uuid.uuid4())[:8]
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        instance = Instance(
            id=instance_id,
            name=name,
            pipeline=pipeline,
            created_at=timestamp,
            modified_at=timestamp
        )

        self.instances[instance_id] = instance
        await self.history_manager.save_instance(instance.to_dict())
        logger.info(f"Created instance: {instance_id} ({name})")

        return instance_id

    async def delete_instance(self, instance_id: str) -> bool:
        """Delete an instance (must be stopped).

        Args:
            instance_id: Instance ID to delete.

        Returns:
            bool: Success status.

        Raises:
            ValueError: If instance is running or not found.
        """
        instance = self.instances.get(instance_id)
        if not instance:
            raise ValueError(f"Instance not found: {instance_id}")

        if instance.status == InstanceStatus.RUNNING:
            raise ValueError(f"Cannot delete running instance: {instance_id}")

        del self.instances[instance_id]
        await self.history_manager.delete_instance(instance_id)
        logger.info(f"Deleted instance: {instance_id}")

        return True

    async def start_instance(self, instance_id: str) -> bool:
        """Start a pipeline instance.

        Args:
            instance_id: Instance ID to start.

        Returns:
            bool: Success status.
        """
        instance = self.instances.get(instance_id)
        if not instance:
            raise ValueError(f"Instance not found: {instance_id}")

        if instance.status == InstanceStatus.RUNNING:
            logger.warning(f"Instance already running: {instance_id}")
            return True

        instance.status = InstanceStatus.STARTING
        instance.error_message = None
        await self._notify_status_change(instance_id, "starting")

        try:
            # Build gst-launch-1.0 command
            cmd = ["gst-launch-1.0", "-e"]
            cmd.extend(self._parse_pipeline(instance.pipeline))

            logger.debug(f"Starting pipeline: {' '.join(cmd)}")

            # Start process
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            self.processes[instance_id] = process
            instance.pid = process.pid
            instance.status = InstanceStatus.RUNNING
            instance.uptime_start = time.time()
            instance.retry_count = 0

            await self._notify_status_change(instance_id, "running")
            logger.info(f"Started instance: {instance_id} (PID: {process.pid})")

            # Monitor process in background
            asyncio.create_task(self._monitor_process(instance_id, process))

            return True

        except Exception as e:
            instance.status = InstanceStatus.ERROR
            instance.error_message = str(e)
            await self._notify_status_change(instance_id, "error")
            logger.error(f"Failed to start instance {instance_id}: {e}")
            return False

    def _parse_pipeline(self, pipeline: str) -> List[str]:
        """Parse pipeline string into arguments.

        Handles quoted strings and special characters.
        """
        # Simple split for now - could be enhanced for complex quoting
        import shlex
        try:
            return shlex.split(pipeline)
        except ValueError:
            # Fallback to simple split
            return pipeline.split()

    async def _monitor_process(
        self,
        instance_id: str,
        process: asyncio.subprocess.Process
    ) -> None:
        """Monitor a running process for completion/errors."""
        instance = self.instances.get(instance_id)
        if not instance:
            return

        try:
            stdout, stderr = await process.communicate()
            exit_code = process.returncode

            if instance_id not in self.instances:
                return  # Instance was deleted

            instance = self.instances[instance_id]

            # Store stderr output in error logs
            if stderr:
                stderr_lines = stderr.decode(errors="replace").strip().split("\n")
                # Keep last 100 lines
                instance.error_logs.extend(stderr_lines)
                instance.error_logs = instance.error_logs[-100:]

            if exit_code == 0:
                logger.info(f"Instance {instance_id} completed normally")
                instance.status = InstanceStatus.STOPPED
                await self._notify_status_change(instance_id, "stopped")
            else:
                error_msg = stderr.decode(errors="replace") if stderr else f"Exit code: {exit_code}"
                logger.error(f"Instance {instance_id} failed: {error_msg[:200]}")
                await self._handle_error(instance_id, error_msg[:500])

        except asyncio.CancelledError:
            logger.debug(f"Monitor cancelled for {instance_id}")
        except Exception as e:
            logger.error(f"Monitor error for {instance_id}: {e}")

        finally:
            if instance_id in self.processes:
                del self.processes[instance_id]

    async def _handle_error(self, instance_id: str, error: str) -> None:
        """Handle pipeline error with recovery logic."""
        instance = self.instances.get(instance_id)
        if not instance:
            return

        # Check if transient error
        is_transient = any(t in error.lower() for t in TRANSIENT_ERRORS)
        is_fatal = any(f in error.lower() for f in FATAL_ERRORS)

        if is_transient and not is_fatal and instance.recovery.auto_restart:
            if instance.retry_count < instance.recovery.max_retries:
                instance.retry_count += 1
                logger.info(
                    f"Retrying instance {instance_id} "
                    f"({instance.retry_count}/{instance.recovery.max_retries})"
                )
                await asyncio.sleep(instance.recovery.retry_delay_seconds)
                await self.start_instance(instance_id)
                return

        # Fatal error or max retries exceeded
        instance.status = InstanceStatus.ERROR
        instance.error_message = error
        await self._notify_status_change(instance_id, "error")

    async def stop_instance(self, instance_id: str) -> bool:
        """Stop a running pipeline instance.

        Args:
            instance_id: Instance ID to stop.

        Returns:
            bool: Success status.
        """
        instance = self.instances.get(instance_id)
        if not instance:
            raise ValueError(f"Instance not found: {instance_id}")

        if instance.status != InstanceStatus.RUNNING:
            logger.warning(f"Instance not running: {instance_id}")
            return True

        instance.status = InstanceStatus.STOPPING
        await self._notify_status_change(instance_id, "stopping")

        # Import signal module (add to top of file if missing, but we can access via signal.SIGINT)
        import signal

        process = self.processes.get(instance_id)
        if process:
            try:
                # Use SIGINT (CTRL+C) to allow gst-launch to handle EOS and cleanup drivers
                process.send_signal(signal.SIGINT)
                try:
                    # Give it 10 seconds to shutdown cleanly (hardware encoders can be slow)
                    await asyncio.wait_for(process.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning(f"Force killing instance: {instance_id}")
                    process.kill()
                    await process.wait()
            except ProcessLookupError:
                pass  # Process already gone

        instance.status = InstanceStatus.STOPPED
        instance.pid = None
        instance.uptime_start = None
        await self._notify_status_change(instance_id, "stopped")
        logger.info(f"Stopped instance: {instance_id}")

        return True

    async def stop_all(self) -> None:
        """Stop all running instances."""
        running = [
            iid for iid, inst in self.instances.items()
            if inst.status == InstanceStatus.RUNNING
        ]
        for instance_id in running:
            await self.stop_instance(instance_id)

    def get_instance_status(self, instance_id: str) -> dict:
        """Get detailed status for an instance.

        Args:
            instance_id: Instance ID.

        Returns:
            dict: Status information.
        """
        instance = self.instances.get(instance_id)
        if not instance:
            raise ValueError(f"Instance not found: {instance_id}")

        uptime = None
        if instance.uptime_start and instance.status == InstanceStatus.RUNNING:
            uptime = int(time.time() - instance.uptime_start)

        return {
            "status": instance.status.value,
            "pid": instance.pid,
            "uptime": uptime,
            "error": instance.error_message,
            "retry_count": instance.retry_count,
            "has_logs": len(instance.error_logs) > 0
        }

    async def update_pipeline(self, instance_id: str, pipeline: str) -> bool:
        """Update pipeline CLI for an instance (must be stopped).

        Args:
            instance_id: Instance ID.
            pipeline: New pipeline CLI string.

        Returns:
            bool: Success status.
        """
        instance = self.instances.get(instance_id)
        if not instance:
            raise ValueError(f"Instance not found: {instance_id}")

        if instance.status == InstanceStatus.RUNNING:
            raise ValueError(f"Cannot update running instance: {instance_id}")

        instance.pipeline = pipeline
        instance.modified_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        await self.history_manager.save_instance(instance.to_dict())
        logger.info(f"Updated pipeline for instance: {instance_id}")

        return True

    def get_instance_logs(self, instance_id: str, lines: int = 50) -> List[str]:
        """Get error logs for an instance.

        Args:
            instance_id: Instance ID.
            lines: Maximum number of lines to return.

        Returns:
            List of log lines.
        """
        instance = self.instances.get(instance_id)
        if not instance:
            raise ValueError(f"Instance not found: {instance_id}")

        return instance.error_logs[-lines:]

    def clear_instance_logs(self, instance_id: str) -> bool:
        """Clear error logs for an instance.

        Args:
            instance_id: Instance ID.

        Returns:
            bool: Success status.
        """
        instance = self.instances.get(instance_id)
        if not instance:
            raise ValueError(f"Instance not found: {instance_id}")

        instance.error_logs = []
        logger.info(f"Cleared logs for instance: {instance_id}")
        return True
