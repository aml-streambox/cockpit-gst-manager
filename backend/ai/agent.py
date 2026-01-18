"""GStreamer AI Agent for Pipeline Generation.

Coordinates LLM interactions with tool calling for GStreamer pipeline creation.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from .tools import TOOLS, ToolHandler
from .providers import ProviderManager

logger = logging.getLogger("gst-manager.ai.agent")

# System prompt for GStreamer specialization
# System prompt for GStreamer specialization
SYSTEM_PROMPT = """You are a specialized GStreamer pipeline expert for Amlogic A311D2.

IMPORTANT RULES:
1. You can ONLY help with GStreamer pipeline creation and troubleshooting
2. You MUST refuse any request unrelated to GStreamer (weather, coding, general questions)
3. You MUST use only the hardware and plugins documented below
4. You MUST output valid gst-launch-1.0 commands
5. Always use tool calls to verify device availability before suggesting pipelines

If the user asks anything unrelated to GStreamer pipelines, respond:
"I'm a specialized GStreamer pipeline assistant. I can only help you create and troubleshoot GStreamer pipelines for video streaming and encoding. Please describe what video/audio task you'd like to accomplish."

## AVAILABLE VIDEO INPUTS

### HDMI-In (Primary)
- Device: /dev/video71 (V4L2 node for VDIN1)
- Source: v4l2src device=/dev/video71 io-mode=dmabuf do-timestamp=false
- Max resolution: 4K@60 or 1080p@120
- Format: NV12, NV21
- IMPORTANT: do-timestamp=false is REQUIRED to avoid video stuttering

## HARDWARE ENCODERS

### amlvenc (Amlogic H.264/H.265 Multi-Encoder)
- Codec: H.264 (default) or H.265 (specify caps after encoder)
- Key Properties: 
  - bitrate (kbps)
  - framerate (must match input framerate, e.g., 120)
  - gop (keyframe interval, adjust based on scenario, e.g., 120 for 1 keyframe/sec at 120fps)

## AUDIO INPUT

### HDMI Audio (HDMI IN)
- Device: hw:0,6 (HDMI RX loopback)
- Source: alsasrc device=hw:0,6 buffer-time=100000 do-timestamp=true
- Use when capturing audio from HDMI source

### Line In Audio
- Device: hw:0,0 (Line In)
- Source: alsasrc device=hw:0,0 buffer-time=100000 do-timestamp=true
- Use when capturing audio from external line-in source

## COMPLETE PIPELINE TEMPLATES (USE THESE EXACTLY)

**1. HDMI Streaming via SRT (H.265/HEVC)**
Use this EXACT structure for SRT streaming. Key parameters:
- do-timestamp=false on v4l2src (prevents stuttering)
- framerate= in amlvenc (MUST match input framerate)
- gop= in amlvenc (MUST be set for proper rate control)
- latency=0 in mpegtsmux (minimize delay)
- wait-for-connection=false in srtsink (passive/server mode)
- leaky=downstream on queues (drop old frames on overflow)
- Audio device: Use hw:0,6 for HDMI IN audio, or hw:0,0 for Line In audio

gst-launch-1.0 -e -v \\
  v4l2src device=/dev/video71 io-mode=dmabuf ! \\
  video/x-raw,format=NV12,framerate=60/1 ! \\
  queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! \\
  amlvenc bitrate=10000 framerate=60 gop=60 ! video/x-h265 ! h265parse config-interval=-1 ! \\
  queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! \\
  mux. \\
  alsasrc device=hw:0,6 buffer-time=100000 ! \\
  audio/x-raw,rate=48000,channels=2,format=S16LE ! \\
  queue max-size-buffers=0 max-size-time=500000000 max-size-bytes=0 ! \\
  audioconvert ! audioresample ! \\
  avenc_aac bitrate=128000 ! aacparse ! \\
  queue max-size-buffers=0 max-size-time=500000000 max-size-bytes=0 ! \\
  mux. \\
  mpegtsmux name=mux alignment=7 ! \\
  srtsink uri="srt://:8888" wait-for-connection=false latency=200

Note: Change alsasrc device from hw:0,6 (HDMI IN) to hw:0,0 (Line In) if using Line In audio source.

**2. HDMI Recording to File (H.265/MKV)**
Use this EXACT structure for file recording. Key parameters:
- framerate= in amlvenc (MUST match input framerate)
- gop= in amlvenc (MUST be set for proper rate control)
- Audio device: Use hw:0,6 for HDMI IN audio, or hw:0,0 for Line In audio

gst-launch-1.0 -e -v \\
  v4l2src device=/dev/video71 io-mode=dmabuf ! \\
  video/x-raw,format=NV12,framerate=60/1 ! \\
  queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! \\
  amlvenc bitrate=30000 framerate=60 gop=60 ! video/x-h265 ! h265parse ! \\
  queue max-size-buffers=30 max-size-time=0 max-size-bytes=0 ! \\
  matroskamux name=mux ! filesink location=test71.mkv \\
  alsasrc device=hw:0,6 buffer-time=100000 ! \\
  audio/x-raw,rate=48000,channels=2,format=S16LE ! \\
  queue max-size-buffers=0 max-size-time=500000000 max-size-bytes=0 ! \\
  audioconvert ! audioresample ! \\
  avenc_aac bitrate=128000 ! aacparse ! \\
  queue max-size-buffers=0 max-size-time=500000000 max-size-bytes=0 ! \\
  mux.

Note: Change alsasrc device from hw:0,6 (HDMI IN) to hw:0,0 (Line In) if using Line In audio source.

## INSTRUCTIONS
- Adjust 'bitrate' (in kbps) and 'uri'/'location' based on user request.
- Keep do-timestamp=false on v4l2src to prevent stuttering.
- Match framerate in amlvenc to the input framerate (e.g., 60 for 60fps input).
- Set gop in amlvenc to match framerate (e.g., gop=60 for 60fps) - REQUIRED for proper rate control.
- Use wait-for-connection=false for SRT server/passive mode.
- For audio device: Use hw:0,6 for HDMI IN audio source, or hw:0,0 for Line In audio source.
- Always include audio unless explicitly asked not to.
"""


class GstAgent:
    """AI agent for GStreamer pipeline generation."""

    MAX_TOOL_ITERATIONS = 5

    def __init__(
        self,
        provider_manager: ProviderManager,
        tool_handler: ToolHandler,
        config: Dict[str, Any]
    ):
        self.provider_manager = provider_manager
        self.tool_handler = tool_handler
        self.config = config
        self.max_retries = config.get("settings", {}).get("ai_max_retries", 3)
        self.timeout = config.get("settings", {}).get("ai_timeout_seconds", 120)  # 2 min for tool calls

    async def generate_pipeline(
        self,
        prompt: str,
        provider_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate a GStreamer pipeline from natural language prompt.

        Args:
            prompt: User's natural language request.
            provider_name: Optional specific provider to use.

        Returns:
            Dict with 'pipeline' or 'error' and 'message'.
        """
        provider = self.provider_manager.get_provider(provider_name)
        if not provider:
            return {
                "error": "No AI provider configured",
                "message": "Please add an AI provider in settings."
            }

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        # Tool calling loop
        for iteration in range(self.MAX_TOOL_ITERATIONS):
            logger.debug(f"AI iteration {iteration + 1}/{self.MAX_TOOL_ITERATIONS}")

            try:
                response = await provider.chat_completion(
                    messages=messages,
                    tools=TOOLS,
                    timeout=self.timeout
                )
            except Exception as e:
                logger.error(f"Provider error: {e}")
                return {"error": f"API error: {e}"}

            if "error" in response:
                return response

            # Check for tool calls
            tool_calls = response.get("tool_calls", [])
            if tool_calls:
                # Execute tools and continue conversation
                messages.append({
                    "role": "assistant",
                    "content": response.get("content", ""),
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"])
                            }
                        }
                        for tc in tool_calls
                    ]
                })

                for tc in tool_calls:
                    result = await self.tool_handler.execute(
                        tc["name"],
                        tc["arguments"]
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result)
                    })

                continue  # Next iteration with tool results

            # No tool calls - should have final response
            content = response.get("content", "")
            return self._extract_pipeline(content)

        return {
            "error": "Max iterations reached",
            "message": "AI could not generate pipeline in allowed iterations."
        }

    def _extract_pipeline(self, content: str) -> Dict[str, Any]:
        """Extract pipeline command from AI response.

        Args:
            content: AI response text.

        Returns:
            Dict with 'pipeline' and 'message', or 'error'.
        """
        # Look for gst-launch-1.0 command
        lines = content.split("\n")
        pipeline = None

        for i, line in enumerate(lines):
            line = line.strip()

            # Skip empty lines and markdown
            if not line or line.startswith("```"):
                continue

            # Check for gst-launch command
            if line.startswith("gst-launch"):
                # Might span multiple lines (backslash continuation)
                pipeline_parts = [line]
                j = i + 1
                while j < len(lines) and lines[j - 1].strip().endswith("\\"):
                    pipeline_parts.append(lines[j].strip())
                    j += 1
                pipeline = " ".join(pipeline_parts).replace("\\", "")
                break

            # Check for v4l2src (might be without gst-launch prefix)
            if line.startswith("v4l2src") or line.startswith("videotestsrc"):
                pipeline = line
                break

        if pipeline:
            # Clean up the pipeline
            if pipeline.startswith("gst-launch-1.0 "):
                pipeline = pipeline[len("gst-launch-1.0 "):]
            if pipeline.startswith("-e "):
                pipeline = pipeline[3:]

            return {
                "pipeline": pipeline.strip(),
                "message": content
            }

        # No pipeline found - return full message
        return {
            "message": content,
            "error": "no_pipeline" if "I can only help" in content else None
        }

    async def fix_error(
        self,
        pipeline: str,
        error: str,
        provider_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Analyze error and suggest fix for a pipeline.

        Args:
            pipeline: The failing pipeline.
            error: Error message from GStreamer.
            provider_name: Optional specific provider.

        Returns:
            Dict with 'pipeline' (fixed), 'message', or 'error'.
        """
        provider = self.provider_manager.get_provider(provider_name)
        if not provider:
            return {"error": "No AI provider configured"}

        prompt = f"""The following GStreamer pipeline is failing:

Pipeline: {pipeline}

Error message:
{error}

Please analyze the error and provide a corrected pipeline. Use tool calls to verify device availability if needed."""

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        try:
            response = await provider.chat_completion(
                messages=messages,
                tools=TOOLS,
                timeout=self.timeout
            )

            if "error" in response:
                return response

            content = response.get("content", "")
            return self._extract_pipeline(content)

        except Exception as e:
            logger.error(f"Fix error failed: {e}")
            return {"error": str(e)}
