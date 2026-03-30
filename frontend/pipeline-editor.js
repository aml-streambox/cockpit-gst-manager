/**
 * Pipeline Editor Component
 * 
 * Handles pipeline text input with optional syntax highlighting hints.
 */

const PipelineEditor = {
    // Known GStreamer elements for highlighting
    KNOWN_ELEMENTS: [
        // Sources
        "v4l2src", "alsasrc", "filesrc", "souphttpsrc", "srtsrc", "rtmpsrc",
        // Sinks
        "srtsink", "rtmpsink", "filesink", "splitmuxsink", "fakesink",
        // Encoders
        "aml_h264enc", "aml_h265enc", "x264enc", "x265enc",
        // Decoders
        "amlvdec", "avdec_h264", "avdec_h265",
        // Parsers
        "h264parse", "h265parse", "aacparse", "mpegaudioparse",
        // Muxers
        "mpegtsmux", "flvmux", "matroskamux", "mp4mux",
        // Audio
        "audioconvert", "audioresample", "faac", "lamemp3enc", "opusenc",
        // Video
        "videoconvert", "videoscale", "videorate", "amlge2d",
        // Utility
        "queue", "tee", "compositor", "capsfilter"
    ],

    // Common properties
    COMMON_PROPS: [
        "device", "location", "uri", "bitrate", "profile", "gop",
        "format", "width", "height", "framerate", "channels", "rate"
    ],

    /**
     * Validate pipeline syntax (basic check)
     * @param {string} pipeline - Pipeline string
     * @returns {Object} Validation result
     */
    validate(pipeline) {
        const result = {
            valid: true,
            warnings: [],
            errors: [],
            elements: []
        };

        if (!pipeline || !pipeline.trim()) {
            result.valid = false;
            result.errors.push("Pipeline is empty");
            return result;
        }

        // Check for common issues
        const trimmed = pipeline.trim();

        // Should not start with gst-launch-1.0
        if (trimmed.startsWith("gst-launch")) {
            result.warnings.push("Remove 'gst-launch-1.0' prefix - it's added automatically");
        }

        // Check for balanced quotes
        const singleQuotes = (trimmed.match(/'/g) || []).length;
        const doubleQuotes = (trimmed.match(/"/g) || []).length;
        if (singleQuotes % 2 !== 0) {
            result.valid = false;
            result.errors.push("Unbalanced single quotes");
        }
        if (doubleQuotes % 2 !== 0) {
            result.valid = false;
            result.errors.push("Unbalanced double quotes");
        }

        // Extract elements (words followed by space or !)
        const elementPattern = /(\w+)(?:\s+\w+=|!|\s+!)/g;
        let match;
        while ((match = elementPattern.exec(trimmed)) !== null) {
            result.elements.push(match[1]);
        }

        // Check for unknown elements
        result.elements.forEach(elem => {
            if (!this.KNOWN_ELEMENTS.includes(elem) &&
                !elem.startsWith("video/") &&
                !elem.startsWith("audio/")) {
                result.warnings.push(`Unknown element: ${elem}`);
            }
        });

        // Check for common mistakes
        if (trimmed.includes("shell=")) {
            result.warnings.push("Found 'shell=' - this might be insecure");
        }

        return result;
    },

    /**
     * Get suggestions for an element
     * @param {string} partial - Partial element name
     * @returns {string[]} Matching elements
     */
    getSuggestions(partial) {
        if (!partial) return [];
        const lower = partial.toLowerCase();
        return this.KNOWN_ELEMENTS.filter(e =>
            e.toLowerCase().startsWith(lower)
        ).slice(0, 5);
    },

    /**
     * Format pipeline for display
     * @param {string} pipeline - Pipeline string
     * @param {boolean} multiline - Whether to format across lines
     * @returns {string} Formatted pipeline
     */
    format(pipeline, multiline = false) {
        if (!multiline) return pipeline;

        // Split on ! and join with newlines
        return pipeline
            .split("!")
            .map(s => s.trim())
            .filter(s => s)
            .join(" ! \\\n    ");
    },

    /**
     * Generate a basic pipeline template
     * @param {string} type - Pipeline type
     * @returns {string} Pipeline template
     */
    getTemplate(type) {
        const templates = {
            "hdmi-srt":
                'v4l2src device=/dev/vdin1 ! video/x-raw,format=NV12 ! queue ! aml_h264enc bitrate=20000000 profile=high gop=60 ! h264parse ! mpegtsmux ! srtsink uri="srt://0.0.0.0:5000?mode=listener"',

            "hdmi-srt-audio":
                'v4l2src device=/dev/vdin1 ! video/x-raw,format=NV12 ! aml_h264enc bitrate=20000000 ! h264parse ! mux. alsasrc device=hw:0,0 ! audioconvert ! faac bitrate=128000 ! aacparse ! mux. mpegtsmux name=mux ! srtsink uri="srt://0.0.0.0:5000?mode=listener"',

            "hdmi-file":
                'v4l2src device=/dev/vdin1 ! video/x-raw,format=NV12 ! aml_h264enc bitrate=30000000 ! h264parse ! mpegtsmux ! filesink location=/mnt/sdcard/recording.ts',

            "hdmi-rtmp":
                'v4l2src device=/dev/vdin1 ! video/x-raw,format=NV12 ! aml_h264enc bitrate=8000000 ! h264parse ! flvmux ! rtmpsink location="rtmp://server/live/streamkey"',

            "usb-srt":
                'v4l2src device=/dev/video0 ! videoconvert ! aml_h264enc bitrate=5000000 ! h264parse ! mpegtsmux ! srtsink uri="srt://0.0.0.0:5001?mode=listener"'
        };

        return templates[type] || templates["hdmi-srt"];
    }
};

// Expose globally for use in gst-manager.js
window.PipelineEditor = PipelineEditor;
