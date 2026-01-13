#!/usr/bin/env python3
"""GStreamer Manager Daemon - Entry Point.

This is the main entry point for the gst-manager daemon.
It sets up the D-Bus server and runs the asyncio event loop.
"""

import asyncio
import logging
import signal
import sys
import os
import json
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from api import GstManagerService
from instances import InstanceManager
from discovery import DiscoveryManager
from history import HistoryManager
from events import EventManager

# Configuration paths
CONFIG_DIR = Path("/var/lib/gst-manager")
CONFIG_FILE = CONFIG_DIR / "config.json"
INSTANCES_DIR = CONFIG_DIR / "instances"

# Default configuration
DEFAULT_CONFIG = {
    "ai_providers": [],
    "active_provider": None,
    "settings": {
        "ai_max_retries": 3,
        "ai_timeout_seconds": 30,
        "poll_interval_seconds": 5,
        "history_max_files": 100
    },
    "proxy": None
}

# Set up logging
logging.basicConfig(
    level=logging.DEBUG if os.environ.get("GST_MANAGER_DEBUG") else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("gst-manager")


def load_config() -> dict:
    """Load configuration from file or create default.

    Returns:
        dict: Configuration dictionary.
    """
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                logger.info(f"Loaded configuration from {CONFIG_FILE}")
                return config
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load config: {e}")
            return DEFAULT_CONFIG.copy()
    else:
        logger.info("No config file found, using defaults")
        return DEFAULT_CONFIG.copy()


def ensure_directories() -> None:
    """Ensure required directories exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
    logger.debug(f"Ensured directories: {CONFIG_DIR}, {INSTANCES_DIR}")


class GstManagerDaemon:
    """Main daemon class for GStreamer Manager."""

    def __init__(self):
        self.config = load_config()
        self.loop = None
        self.service = None
        self.running = False

        # Initialize managers
        self.history_manager = HistoryManager(INSTANCES_DIR)
        self.discovery_manager = DiscoveryManager(CONFIG_DIR)
        self.instance_manager = InstanceManager(self.history_manager)
        self.event_manager = None  # Initialized after service starts

    async def start(self) -> None:
        """Start the daemon and D-Bus service."""
        logger.info("Starting GStreamer Manager Daemon")
        self.running = True

        # Perform initial hardware discovery
        await self.discovery_manager.refresh()

        # Load existing instances
        await self.instance_manager.load_instances()

        # Create and start D-Bus service
        self.service = GstManagerService(
            instance_manager=self.instance_manager,
            discovery_manager=self.discovery_manager,
            history_manager=self.history_manager,
            config=self.config
        )
        await self.service.start()

        logger.info("D-Bus server started on org.cockpit.GstManager")

        # Start event monitoring (HDMI detection)
        self.event_manager = EventManager(
            instance_manager=self.instance_manager,
            service=self.service
        )
        await self.event_manager.start()

        # Connect event manager to D-Bus interface
        if self.service.interface:
            self.service.interface.event_manager = self.event_manager

        logger.info("Event monitoring started")

        # Auto-start instances with "boot" trigger
        await self._start_boot_instances()

        # Initialize AI agent
        try:
            from ai.providers import ProviderManager
            from ai.tools import ToolHandler
            from ai.agent import GstAgent

            provider_manager = ProviderManager(self.config)
            tool_handler = ToolHandler(self.discovery_manager, self.instance_manager)
            self.ai_agent = GstAgent(provider_manager, tool_handler, self.config)

            if self.service.interface:
                self.service.interface.ai_agent = self.ai_agent

            logger.info("AI agent initialized")
        except ImportError as e:
            logger.warning(f"AI module not available: {e}")
        except Exception as e:
            logger.error(f"Failed to initialize AI agent: {e}")

        # Keep running until stopped
        while self.running:
            await asyncio.sleep(1)

    async def _start_boot_instances(self) -> None:
        """Auto-start instances configured with 'boot' trigger."""
        boot_instances = [
            instance for instance in self.instance_manager.instances.values()
            if instance.autostart and instance.trigger_event == "boot"
        ]

        if not boot_instances:
            logger.debug("No boot-trigger instances to start")
            return

        logger.info(f"Starting {len(boot_instances)} boot-trigger instance(s)")

        for instance in boot_instances:
            if instance.status.value == "stopped":
                try:
                    logger.info(f"Auto-starting boot instance: {instance.id}")
                    await self.instance_manager.start_instance(instance.id)
                except Exception as e:
                    logger.error(f"Failed to auto-start {instance.id}: {e}")

    async def stop(self) -> None:
        """Stop the daemon gracefully."""
        logger.info("Stopping GStreamer Manager Daemon")
        self.running = False

        # Stop event monitoring
        if self.event_manager:
            await self.event_manager.stop()

        # Stop all running instances
        await self.instance_manager.stop_all()

        # Stop D-Bus service
        if self.service:
            await self.service.stop()

        logger.info("Daemon stopped")


def main():
    """Main entry point."""
    ensure_directories()

    daemon = GstManagerDaemon()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Handle signals for graceful shutdown
    def signal_handler(sig):
        logger.info(f"Received signal {sig}, shutting down...")
        loop.create_task(daemon.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

    try:
        loop.run_until_complete(daemon.start())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        loop.run_until_complete(daemon.stop())
        loop.close()
        logger.info("Event loop closed")


if __name__ == "__main__":
    main()
