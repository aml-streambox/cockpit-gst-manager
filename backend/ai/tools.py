"""AI Tool Definitions for GStreamer Pipeline Generation.

Defines OpenAI-compatible tool schemas and handlers for the AI agent.
"""

import asyncio
import json
import logging
import subprocess
from typing import Dict, Any, List, Optional

logger = logging.getLogger("gst-manager.ai.tools")


# OpenAI-compatible tool definitions
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_board_info",
            "description": "Get current hardware status including video inputs, encoders, "
                          "storage, and custom plugins",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_video_devices",
            "description": "List all video input devices (HDMI-In, USB cameras) with their "
                          "capabilities",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_storage",
            "description": "Check available storage locations and their free space",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Optional: specific path to check"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_encoder_info",
            "description": "Get detailed information about hardware encoders including "
                          "supported properties",
            "parameters": {
                "type": "object",
                "properties": {
                    "encoder": {
                        "type": "string",
                        "enum": ["h264", "h265", "all"],
                        "description": "Which encoder to query"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_gst_element_info",
            "description": "Get properties and pads for a GStreamer element",
            "parameters": {
                "type": "object",
                "properties": {
                    "element": {
                        "type": "string",
                        "description": "GStreamer element name (e.g., srtsink, aml_h264enc)"
                    }
                },
                "required": ["element"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "validate_pipeline",
            "description": "Check if a GStreamer pipeline is syntactically valid",
            "parameters": {
                "type": "object",
                "properties": {
                    "pipeline": {
                        "type": "string",
                        "description": "The gst-launch-1.0 pipeline to validate"
                    }
                },
                "required": ["pipeline"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_running_instances",
            "description": "List all running GStreamer pipeline instances and their status",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    }
]


class ToolHandler:
    """Executes AI tool calls using discovery and instance managers."""

    def __init__(self, discovery_manager, instance_manager):
        self.discovery_manager = discovery_manager
        self.instance_manager = instance_manager

    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool call and return result.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool arguments.

        Returns:
            Tool result as dictionary.
        """
        handler = getattr(self, f"_tool_{tool_name}", None)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}"}

        try:
            result = await handler(arguments)
            logger.debug(f"Tool {tool_name} returned: {result}")
            return result
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}")
            return {"error": str(e)}

    async def _tool_get_board_info(self, args: Dict) -> Dict:
        """Get complete hardware discovery information."""
        return self.discovery_manager.get_context()

    async def _tool_list_video_devices(self, args: Dict) -> List[Dict]:
        """List video input devices."""
        ctx = self.discovery_manager.get_context()
        return ctx.get("video_inputs", [])

    async def _tool_check_storage(self, args: Dict) -> List[Dict]:
        """Check storage availability."""
        ctx = self.discovery_manager.get_context()
        storage = ctx.get("storage", [])

        path = args.get("path")
        if path:
            return [s for s in storage if s.get("path") == path]
        return storage

    async def _tool_get_encoder_info(self, args: Dict) -> Dict:
        """Get encoder information."""
        encoder = args.get("encoder", "all")

        encoders = {
            "amlvenc": {
                "codec": "H.264/H.265",
                "max_resolution": "4096x2160",
                "max_bitrate": 0,  # No practical limit
                "properties": {
                    "bitrate": {"type": "int", "min": 0, "max": 200000000,
                               "default": 10000000},
                    "gop": {"type": "int", "min": 1, "max": 300, "default": 30},
                    "framerate": {"type": "int", "min": 0, "max": 240, "default": 30}
                }
            }
        }

        if encoder == "h264" or encoder == "h265":
            return encoders  # Return amlvenc for both queries
        return encoders

    async def _tool_get_gst_element_info(self, args: Dict) -> Dict:
        """Get GStreamer element info via gst-inspect."""
        element = args.get("element")
        if not element:
            return {"error": "element parameter required"}

        try:
            proc = await asyncio.create_subprocess_exec(
                "gst-inspect-1.0", element,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)

            if proc.returncode != 0:
                return {"error": f"Element not found: {element}"}

            output = stdout.decode()
            return self._parse_gst_inspect(element, output)

        except asyncio.TimeoutError:
            return {"error": "gst-inspect timed out"}
        except Exception as e:
            return {"error": str(e)}

    def _parse_gst_inspect(self, element: str, output: str) -> Dict:
        """Parse gst-inspect output into structured data."""
        result = {
            "element": element,
            "properties": [],
            "description": ""
        }

        lines = output.split("\n")
        in_properties = False

        for line in lines:
            if "Element Properties:" in line:
                in_properties = True
                continue

            if in_properties and line.startswith("  "):
                # Parse property line
                parts = line.strip().split(":")
                if len(parts) >= 2:
                    result["properties"].append(parts[0].strip())

            if "Description" in line and ":" in line:
                result["description"] = line.split(":", 1)[1].strip()

        return result

    async def _tool_validate_pipeline(self, args: Dict) -> Dict:
        """Validate a GStreamer pipeline."""
        pipeline = args.get("pipeline")
        if not pipeline:
            return {"error": "pipeline parameter required"}

        try:
            # Use gst-launch with --gst-parse-only to validate
            cmd = ["gst-launch-1.0", "--gst-parse-only"] + pipeline.split()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)

            if proc.returncode == 0:
                # Extract element names
                elements = []
                for part in pipeline.split("!"):
                    element = part.strip().split()[0] if part.strip() else None
                    if element and not element.startswith("-"):
                        elements.append(element)

                return {
                    "valid": True,
                    "elements": elements,
                    "warnings": []
                }
            else:
                error = stderr.decode().strip()
                return {
                    "valid": False,
                    "error": error[:200],
                    "suggestion": self._suggest_fix(error)
                }

        except asyncio.TimeoutError:
            return {"valid": False, "error": "Validation timed out"}
        except Exception as e:
            return {"valid": False, "error": str(e)}

    def _suggest_fix(self, error: str) -> Optional[str]:
        """Suggest fix based on error message."""
        error_lower = error.lower()

        if "no element" in error_lower:
            return "Check element name spelling or verify plugin is installed"
        if "could not link" in error_lower:
            return "Check caps compatibility between elements"
        if "device not found" in error_lower:
            return "Verify device path exists (use list_video_devices tool)"

        return None

    async def _tool_get_running_instances(self, args: Dict) -> List[Dict]:
        """Get all running instances."""
        instances = self.instance_manager.list_instances()
        return [
            {
                "id": i["id"],
                "name": i["name"],
                "status": i["status"],
                "pipeline": i["pipeline"][:100]
            }
            for i in instances
            if i["status"] == "running"
        ]
