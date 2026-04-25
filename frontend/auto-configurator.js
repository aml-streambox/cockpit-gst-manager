/**
 * Auto Configurator - UI Logic
 * 
 * Manages single auto instance configuration for HDMI passthrough capture.
 * 
 * Features:
 * - Single auto instance (creates/replaces)
 * - HDMI TX state monitoring
 * - Pipeline preview with real-time updates
 * - Selectable SRT / RTMP / RTSP streaming output
 * - Optional MPEG-TS recording
 */

class AutoConfigurator {
    constructor() {
        this.config = this.getDefaultConfig();
        this.hasExistingInstance = false;
        this.autoInstanceId = null;
        this.passthroughState = null;
    }

    getDefaultConfig() {
        return {
            capture_source: 'vfmcap',
            gop_interval_seconds: 1.0,
            output_codec: 'h265',
            bitrate_kbps: 20000,
            rc_mode: 1,  // CBR
            gop_pattern: 0,
            lossless_enable: false,
            fixed_qp_value: 28,
            audio_source: 'hdmi_rx',
            output_transport: 'srt',
            srt_port: 8888,
            srt_wait_for_connection: false,
            rtmp_url: 'rtmp://127.0.0.1/live/stream',
            rtsp_url: 'rtsp://127.0.0.1:8554/live/stream',
            recording_enabled: false,
            recording_path: '/mnt/sdcard/recordings/capture.ts',
            autostart_on_ready: true,
            use_hdr: true,
            color_mode: 'passthrough',
            signal_debounce_seconds: 2.0,
            max_restart_retries: 5,
            restart_backoff_base: 1.0,
            restart_backoff_max: 30.0
        };
    }

    async init() {
        this.setupEventListeners();
        this.startStatusMonitoring();
        await this.loadConfig();
        // Initial preview after everything is loaded
        setTimeout(() => this.updatePreview(), 500);
        
        // Clean up when page unloads
        window.addEventListener('beforeunload', () => {
            if (this._statusPollInterval) {
                clearInterval(this._statusPollInterval);
            }
            if (this._passthroughPollInterval) {
                clearInterval(this._passthroughPollInterval);
            }
        });
    }

    setupEventListeners() {
        // Form field changes - auto-preview
        const inputs = [
            'auto-capture-source', 'auto-gop-interval', 'auto-output-codec', 'auto-bitrate', 'auto-rc-mode',
            'auto-gop-pattern', 'auto-lossless-enable', 'auto-fixed-qp-value',
            'auto-audio-source', 'auto-output-transport', 'auto-srt-port',
            'auto-rtmp-url', 'auto-rtsp-url',
            'auto-recording-enabled', 'auto-recording-path', 'auto-autostart',
            'auto-use-hdr', 'auto-color-mode', 'auto-signal-debounce', 'auto-max-restart-retries',
            'auto-restart-backoff-base', 'auto-restart-backoff-max'
        ];
        
        inputs.forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.addEventListener('change', () => this.updatePreview());
                el.addEventListener('input', () => this.debouncedUpdate());
            }
        });

        // Capture source change - update hint text
        const captureSourceEl = document.getElementById('auto-capture-source');
        if (captureSourceEl) {
            captureSourceEl.addEventListener('change', (e) => {
                this.updateCaptureSourceHint(e.target.value);
                this.updatePreview();
            });
        }

        const outputTransportEl = document.getElementById('auto-output-transport');
        if (outputTransportEl) {
            outputTransportEl.addEventListener('change', () => {
                this.updateTransportUI();
                this.updatePreview();
            });
        }

        // Recording toggle
        const recEnable = document.getElementById('auto-recording-enabled');
        if (recEnable) {
            recEnable.addEventListener('change', (e) => {
                const pathGroup = document.getElementById('auto-recording-path-group');
                if (pathGroup) {
                    pathGroup.style.display = e.target.checked ? 'block' : 'none';
                }
                this.updatePreview();
            });
        }

        // Buttons
        const previewBtn = document.getElementById('btn-preview-auto');
        if (previewBtn) {
            previewBtn.addEventListener('click', () => this.updatePreview());
        }

        const saveBtn = document.getElementById('btn-save-auto');
        if (saveBtn) {
            saveBtn.addEventListener('click', () => this.saveConfig());
        }
        
        // Manual start/stop buttons
        const startBtn = document.getElementById('btn-auto-start');
        if (startBtn) {
            startBtn.addEventListener('click', () => this.startInstance());
        }
        
        const stopBtn = document.getElementById('btn-auto-stop');
        if (stopBtn) {
            stopBtn.addEventListener('click', () => this.stopInstance());
        }
    }

    debouncedUpdate() {
        if (this.previewTimeout) clearTimeout(this.previewTimeout);
        this.previewTimeout = setTimeout(() => this.updatePreview(), 500);
    }

    async loadConfig() {
        try {
            const result = await callMethod('GetAutoInstanceConfig');
            const config = JSON.parse(result);
            console.log('Loaded auto config:', config);
            
            // Always use the returned config (defaults + any user overrides)
            this.config = { ...this.getDefaultConfig(), ...config };
            this.populateForm();
            
            // Always start polling for instance status
            this.hasExistingInstance = true;
            this.startInstanceStatusPolling();
            
            // Try to get status immediately, with retry
            let retries = 5;
            while (retries > 0) {
                await this.pollInstanceStatus();
                if (this.autoInstanceId) {
                    console.log('Found auto instance on first try');
                    break;
                }
                console.log(`No auto instance yet, retrying... (${retries} left)`);
                await new Promise(r => setTimeout(r, 1000));
                retries--;
            }
            
            await this.updatePreview();
        } catch (error) {
            console.error('Failed to load auto config:', error);
            // Use defaults on error
            this.config = this.getDefaultConfig();
            this.populateForm();
            this.hasExistingInstance = true; // Still try to poll
            this.startInstanceStatusPolling();
            this.updateInstanceStatusDisplay('off', 'Using defaults');
            await this.updatePreview();
        }
    }
    
    startInstanceStatusPolling() {
        // Poll for instance status every 2 seconds
        this._statusPollInterval = setInterval(() => this.pollInstanceStatus(), 2000);
        // Initial poll
        this.pollInstanceStatus();
    }
    
    async pollInstanceStatus() {
        if (!this.hasExistingInstance) return;
        
        try {
            // Get all instances and find the auto one
            const result = await callMethod('ListInstances');
            const instances = JSON.parse(result);
            console.log('All instances:', instances);
            console.log('Looking for auto instance, checking types:', instances.map(i => ({id: i.id, type: i.instance_type, status: i.status})));
            
            const autoInstance = instances.find(i => i.instance_type === 'auto');
            console.log('Found auto instance:', autoInstance);
            
            if (autoInstance) {
                this.autoInstanceId = autoInstance.id;
                this.updateInstanceStatusDisplay(autoInstance.status, autoInstance.error_message);
            } else {
                this.autoInstanceId = null;
                this.updateInstanceStatusDisplay('off', 'Instance not found');
            }
        } catch (error) {
            console.error('Failed to poll instance status:', error);
        }
    }
    
    updateInstanceStatusDisplay(status, errorMessage) {
        const statusEl = document.getElementById('auto-instance-status');
        const actionsEl = document.getElementById('auto-instance-actions');
        if (!statusEl) return;
        
        const badgeClass = {
            'running': 'gst-status-running',
            'error': 'gst-status-error',
            'stopped': 'gst-status-off',
            'off': 'gst-status-off',
            'starting': 'gst-status-running',
            'stopping': 'gst-status-running',
            'waiting_signal': 'gst-status-waiting_signal'
        }[status] || 'gst-status-off';
        
        const statusText = {
            'running': 'Running',
            'error': errorMessage || 'Error',
            'stopped': 'Stopped (ready to start)',
            'off': 'Not configured',
            'starting': 'Starting...',
            'stopping': 'Stopping...',
            'waiting_signal': errorMessage || 'Waiting for signal'
        }[status] || status;
        
        const badgeText = {
            'running': 'RUNNING',
            'error': 'ERROR',
            'stopped': 'OFF',
            'off': 'OFF',
            'starting': 'STARTING',
            'stopping': 'STOPPING',
            'waiting_signal': 'WAITING'
        }[status] || status.toUpperCase();
        
        statusEl.innerHTML = `
            <span class="gst-status-badge ${badgeClass}">${badgeText}</span>
            <span class="gst-status-text">${statusText}</span>
        `;
        
        // Show/hide action buttons based on state
        if (actionsEl) {
            if (this.hasExistingInstance) {
                actionsEl.style.display = 'flex';
                const startBtn = document.getElementById('btn-auto-start');
                const stopBtn = document.getElementById('btn-auto-stop');
                
                if (startBtn && stopBtn) {
                    if (status === 'running' || status === 'starting' || status === 'waiting_signal') {
                        startBtn.disabled = true;
                        startBtn.style.display = 'none';
                        stopBtn.disabled = status === 'waiting_signal';
                        stopBtn.style.display = 'inline-block';
                    } else {
                        startBtn.disabled = false;
                        startBtn.style.display = 'inline-block';
                        stopBtn.disabled = true;
                        stopBtn.style.display = 'none';
                    }
                }
            } else {
                actionsEl.style.display = 'none';
            }
        }
    }
    
    async startInstance() {
        if (!this.autoInstanceId) {
            showToast('No auto instance configured', 'error');
            return;
        }
        
        try {
            this.updateInstanceStatusDisplay('starting', 'Starting pipeline...');
            await callMethod('StartInstance', this.autoInstanceId);
            showToast('Pipeline started', 'success');
        } catch (error) {
            console.error('Failed to start instance:', error);
            showToast('Failed to start: ' + error.message, 'error');
        }
    }
    
    async stopInstance() {
        if (!this.autoInstanceId) {
            showToast('No auto instance configured', 'error');
            return;
        }
        
        try {
            this.updateInstanceStatusDisplay('stopping', 'Stopping pipeline...');
            await callMethod('StopInstance', this.autoInstanceId);
            showToast('Pipeline stopped', 'success');
        } catch (error) {
            console.error('Failed to stop instance:', error);
            showToast('Failed to stop: ' + error.message, 'error');
        }
    }

    populateForm() {
        const setValue = (id, value) => {
            const el = document.getElementById(id);
            if (el) el.value = value;
        };

        setValue('auto-capture-source', this.config.capture_source || 'vfmcap');
        setValue('auto-gop-interval', this.config.gop_interval_seconds);
        setValue('auto-output-codec', this.config.output_codec || 'h265');
        setValue('auto-output-transport', this.config.output_transport || 'srt');
        setValue('auto-bitrate', this.config.bitrate_kbps);
        setValue('auto-rc-mode', this.config.rc_mode);
        setValue('auto-gop-pattern', this.config.gop_pattern != null ? this.config.gop_pattern : 0);
        setValue('auto-audio-source', this.config.audio_source);
        setValue('auto-srt-port', this.config.srt_port);
        setValue('auto-rtmp-url', this.config.rtmp_url || 'rtmp://127.0.0.1/live/stream');
        setValue('auto-rtsp-url', this.config.rtsp_url || 'rtsp://127.0.0.1:8554/live/stream');
        setValue('auto-recording-path', this.config.recording_path);
        setValue('auto-signal-debounce', this.config.signal_debounce_seconds != null ? this.config.signal_debounce_seconds : 2.0);
        setValue('auto-max-restart-retries', this.config.max_restart_retries != null ? this.config.max_restart_retries : 5);
        setValue('auto-restart-backoff-base', this.config.restart_backoff_base != null ? this.config.restart_backoff_base : 1.0);
        setValue('auto-restart-backoff-max', this.config.restart_backoff_max != null ? this.config.restart_backoff_max : 30.0);

        const recEnable = document.getElementById('auto-recording-enabled');
        if (recEnable) {
            recEnable.checked = this.config.recording_enabled;
        }

        const autostart = document.getElementById('auto-autostart');
        if (autostart) {
            autostart.checked = this.config.autostart_on_ready;
        }

        const useHdr = document.getElementById('auto-use-hdr');
        if (useHdr) {
            useHdr.checked = this.config.use_hdr !== false; // default true
        }

        setValue('auto-color-mode', this.config.color_mode || 'passthrough');

        const losslessEnable = document.getElementById('auto-lossless-enable');
        if (losslessEnable) {
            losslessEnable.checked = this.config.lossless_enable === true;
        }
        setValue('auto-fixed-qp-value', this.config.fixed_qp_value != null ? this.config.fixed_qp_value : 28);

        const pathGroup = document.getElementById('auto-recording-path-group');
        if (pathGroup) {
            pathGroup.style.display = this.config.recording_enabled ? 'block' : 'none';
        }

        this.updateTransportUI();
        
        // Update capture source hint
        this.updateCaptureSourceHint(this.config.capture_source || 'vfmcap');
    }
    
    updateCaptureSourceHint(source) {
        const hintEl = document.getElementById('auto-capture-source-hint');
        if (!hintEl) return;

        const hints = {
            'vfmcap': 'Path A: Low-latency raw capture with Vulkan GPU conversion. ' +
                       'Best for minimal processing overhead. Uses HDMI RX readiness only and does not require HDMI TX.',
            'vdin1': 'Path B: Captures via Amlogic VPP color processing pipeline. ' +
                      'NV21 passthrough for SDR, Vulkan AMLY to P010 for HDR. Requires HDMI TX because it is a loopback / screen-recording path.',
            'v4l2_legacy': 'Legacy v4l2 capture from /dev/video71. Deprecated - ' +
                            'use Path A or B instead for better signal handling and recovery. Treated like an RX-driven capture path.'
        };
        
        hintEl.textContent = hints[source] || '';
    }

    getFormConfig() {
        const getValue = (id, defaultVal) => {
            const el = document.getElementById(id);
            return el ? el.value : defaultVal;
        };

        const getChecked = (id) => {
            const el = document.getElementById(id);
            return el ? el.checked : false;
        };

        return {
            capture_source: getValue('auto-capture-source', 'vfmcap'),
            gop_interval_seconds: parseFloat(getValue('auto-gop-interval', '1.0')),
            output_codec: getValue('auto-output-codec', 'h265'),
            bitrate_kbps: parseInt(getValue('auto-bitrate', '20000')),
            rc_mode: parseInt(getValue('auto-rc-mode', '1')),
            gop_pattern: parseInt(getValue('auto-gop-pattern', '0')),
            lossless_enable: getChecked('auto-lossless-enable'),
            fixed_qp_value: parseInt(getValue('auto-fixed-qp-value', '28')),
            audio_source: getValue('auto-audio-source', 'hdmi_rx'),
            output_transport: getValue('auto-output-transport', 'srt'),
            srt_port: parseInt(getValue('auto-srt-port', '8888')),
            srt_wait_for_connection: false,
            rtmp_url: getValue('auto-rtmp-url', 'rtmp://127.0.0.1/live/stream'),
            rtsp_url: getValue('auto-rtsp-url', 'rtsp://127.0.0.1:8554/live/stream'),
            recording_enabled: getChecked('auto-recording-enabled'),
            recording_path: getValue('auto-recording-path', '/mnt/sdcard/recordings/capture.ts'),
            autostart_on_ready: getChecked('auto-autostart'),
            use_hdr: getChecked('auto-use-hdr'),
            color_mode: getValue('auto-color-mode', 'passthrough'),
            signal_debounce_seconds: parseFloat(getValue('auto-signal-debounce', '2.0')),
            max_restart_retries: parseInt(getValue('auto-max-restart-retries', '5')),
            restart_backoff_base: parseFloat(getValue('auto-restart-backoff-base', '1.0')),
            restart_backoff_max: parseFloat(getValue('auto-restart-backoff-max', '30.0'))
        };
    }

    async updatePreview() {
        try {
            const config = this.getFormConfig();
            console.log('Getting pipeline preview with config:', config);
            
            const result = await callMethod('GetAutoInstancePipelinePreview', JSON.stringify(config));
            console.log('Pipeline preview result:', result);
            
            const previewEl = document.getElementById('auto-pipeline-preview');
            if (previewEl) {
                // Show full command with gst-launch-1.0 -e (automatically added by backend)
                previewEl.textContent = 'gst-launch-1.0 -e ' + result;
            }
        } catch (error) {
            console.error('Failed to get preview:', error);
            const previewEl = document.getElementById('auto-pipeline-preview');
            if (previewEl) {
                previewEl.textContent = 'Error: ' + error.message;
            }
        }
    }

    async saveConfig() {
        try {
            const config = this.getFormConfig();
            const success = await callMethod('SetAutoInstanceConfig', JSON.stringify(config));
            
            if (success) {
                showToast('Auto configuration saved', 'success');
                this.config = config;
                
                // Immediately poll to get updated status
                await this.pollInstanceStatus();
                await refreshInstances();
            } else {
                showToast('Failed to save configuration', 'error');
            }
        } catch (error) {
            console.error('Failed to save:', error);
            showToast('Error: ' + error.message, 'error');
        }
    }



    startStatusMonitoring() {
        // Poll every 2 seconds
        this._passthroughPollInterval = setInterval(async () => {
            try {
                const [passthroughResult, hdmiResult] = await Promise.all([
                    callMethod('GetPassthroughState'),
                    callMethod('GetHdmiStatus')
                ]);
                const runtimeState = {
                    passthrough: JSON.parse(passthroughResult),
                    hdmi: JSON.parse(hdmiResult)
                };
                this.passthroughState = runtimeState;
                this.updateStatusUI(runtimeState);
            } catch (error) {
                console.debug('Failed to get passthrough state:', error);
            }
        }, 2000);

        // Subscribe to D-Bus signals
        if (typeof state !== 'undefined' && state.dbus) {
            state.dbus.subscribe(
                { interface: DBUS_INTERFACE, member: 'PassthroughStateChanged' },
                (path, iface, signal, args) => {
                    const runtimeState = {
                        passthrough: JSON.parse(args[1]),
                        hdmi: this.passthroughState?.hdmi || null
                    };
                    this.passthroughState = runtimeState;
                    this.updateStatusUI(runtimeState);
                }
            );

            state.dbus.subscribe(
                { interface: DBUS_INTERFACE, member: 'HdmiSignalChanged' },
                (path, iface, signal, args) => {
                    const [available, resolution] = args;
                    const runtimeState = {
                        passthrough: this.passthroughState?.passthrough || null,
                        hdmi: {
                            ...(this.passthroughState?.hdmi || {}),
                            signal_locked: available,
                            resolution: resolution || ''
                        }
                    };
                    this.passthroughState = runtimeState;
                    this.updateStatusUI(runtimeState);
                }
            );
        }
    }

    updateStatusUI(runtimeState) {
        if (!runtimeState) return;

        const passthrough = runtimeState.passthrough || {};
        const hdmi = runtimeState.hdmi || {};
        const captureSourceEl = document.getElementById('auto-capture-source');
        const captureSource = captureSourceEl ? captureSourceEl.value : (this.config.capture_source || 'vfmcap');
        const isTxDependent = captureSource === 'vdin1';

        const setDot = (id, status) => {
            const dot = document.getElementById(id);
            if (dot) {
                dot.className = 'gst-hdmi-dot ' + status;
            }
        };

        const setText = (id, text) => {
            const el = document.getElementById(id);
            if (el) {
                el.textContent = text;
            }
        };

        // HDMI RX - check if stable (not just connected)
        if (passthrough.rx_stable || hdmi.signal_locked) {
            setDot('auto-hdmi-rx-dot', 'connected');
            setText('auto-hdmi-rx-text', 'Connected (Stable)');
        } else if (passthrough.rx_connected || hdmi.cable_connected) {
            setDot('auto-hdmi-rx-dot', 'unstable');
            setText('auto-hdmi-rx-text', 'Connected (Unstable)');
        } else {
            setDot('auto-hdmi-rx-dot', 'disconnected');
            setText('auto-hdmi-rx-text', 'Disconnected');
        }

        // HDMI TX - check if ready (ready=1 and has valid resolution)
        if (passthrough.tx_ready) {
            setDot('auto-hdmi-tx-dot', 'connected');
            setText('auto-hdmi-tx-text', 'Ready');
        } else if (passthrough.tx_connected) {
            setDot('auto-hdmi-tx-dot', 'unstable');
            setText('auto-hdmi-tx-text', 'Not Ready');
        } else {
            setDot('auto-hdmi-tx-dot', 'disconnected');
            setText('auto-hdmi-tx-text', isTxDependent ? 'Disconnected' : 'Optional');
        }

        const rxReady = Boolean(
            passthrough.rx_stable || (
                hdmi.signal_locked && ((hdmi.width || 0) > 0 || (hdmi.resolution && hdmi.resolution !== ''))
            )
        );
        const captureReady = isTxDependent ? Boolean(passthrough.can_capture) : rxReady;

        if (captureReady) {
            setDot('auto-passthrough-dot', 'active');
            setText('auto-passthrough-text', 'Ready');
        } else {
            setDot('auto-passthrough-dot', 'inactive');
            setText('auto-passthrough-text', 'Not Ready');
        }

        setText(
            'auto-capture-readiness-hint',
            isTxDependent
                ? 'Path B uses HDMI TX loopback, so RX must be stable and TX must be ready.'
                : 'Path A and legacy capture use HDMI RX only. HDMI TX is optional.'
        );

        const detectedWidth = isTxDependent ? passthrough.width : (hdmi.width || passthrough.width);
        const detectedHeight = isTxDependent ? passthrough.height : (hdmi.height || passthrough.height);
        const detectedFps = isTxDependent ? passthrough.framerate : (hdmi.fps || passthrough.framerate || 60);
        const resolutionSource = isTxDependent ? 'TX' : 'RX';

        if (detectedWidth && detectedHeight) {
            setText('auto-detected-res', `Detected (${resolutionSource}): ${detectedWidth}x${detectedHeight}p${detectedFps}`);
        } else {
            setText('auto-detected-res', 'Detected: -');
        }

        // HDR source detection and pipeline mode
        const hdrStatusEl = document.getElementById('auto-hdr-status');
        const detectedHdrEl = document.getElementById('auto-detected-hdr');
        const useHdrChecked = document.getElementById('auto-use-hdr');
        const losslessChecked = document.getElementById('auto-lossless-enable');
        const losslessHintEl = document.getElementById('auto-lossless-hint');
        const codecSelect = document.getElementById('auto-output-codec');
        const rcModeSelect = document.getElementById('auto-rc-mode');
        const fixedQpGroup = document.getElementById('auto-fixed-qp-group');
        const hdrEnabled = useHdrChecked ? useHdrChecked.checked : true;
        const losslessEnabled = losslessChecked ? losslessChecked.checked : false;
        const outputCodec = codecSelect ? codecSelect.value : 'h265';
        const rcMode = rcModeSelect ? rcModeSelect.value : '1';
        const sourceIsHdr = passthrough.source_is_hdr || (hdmi.hdr_info > 0) || ((hdmi.color_depth || passthrough.color_depth || 0) >= 10);
        const colorDepth = hdmi.color_depth || passthrough.color_depth || 8;

        if (fixedQpGroup) {
            fixedQpGroup.style.display = rcMode === '2' ? 'block' : 'none';
        }
        
        // Pipeline mode label depends on capture source
        const pipelineLabel = {
            'vfmcap': 'P010 + Vulkan',
            'vdin1': 'Vulkan AMLY\u2192P010',
            'v4l2_legacy': 'ENCODED + Vulkan'
        }[captureSource] || 'Vulkan';

        // Badge shows the *pipeline mode* (what will actually run), not just the source
        if (detectedHdrEl) {
            if (sourceIsHdr && hdrEnabled) {
                // HDR source + HDR enabled → HDR 10-bit pipeline
                detectedHdrEl.textContent = `HDR ${colorDepth}-bit`;
                detectedHdrEl.className = 'gst-hdr-badge hdr-active';
            } else if (!sourceIsHdr && hdrEnabled && rxReady) {
                // SDR source + HDR enabled → 10-bit pipeline (force mode)
                detectedHdrEl.textContent = '10-bit';
                detectedHdrEl.className = 'gst-hdr-badge hdr-active';
            } else if (sourceIsHdr && !hdrEnabled && rxReady) {
                // HDR source + HDR disabled → show source is HDR but pipeline is SDR
                detectedHdrEl.textContent = `HDR ${colorDepth}-bit`;
                detectedHdrEl.className = 'gst-hdr-badge hdr-inactive';
            } else if (rxReady) {
                // SDR source + HDR disabled → plain SDR
                detectedHdrEl.textContent = `SDR ${colorDepth}-bit`;
                detectedHdrEl.className = 'gst-hdr-badge hdr-inactive';
            } else {
                detectedHdrEl.textContent = '';
                detectedHdrEl.className = 'gst-hdr-badge';
            }
        }

        // Status line describes both source and pipeline state
        if (hdrStatusEl) {
            if (!rxReady) {
                hdrStatusEl.textContent = 'Source: No signal';
                hdrStatusEl.className = 'gst-hdr-status';
            } else if (losslessEnabled && outputCodec !== 'h265') {
                hdrStatusEl.textContent = 'Lossless requires H.265. Current selection will fall back to normal encoding.';
                hdrStatusEl.className = 'gst-hdr-status hdr-disabled';
            } else if (losslessEnabled) {
                hdrStatusEl.textContent = 'Lossless HEVC enabled - recommended only for 720p60 or lower';
                hdrStatusEl.className = 'gst-hdr-status hdr-disabled';
            } else if (sourceIsHdr && hdrEnabled) {
                hdrStatusEl.textContent = `HDR ${colorDepth}-bit pipeline active (${pipelineLabel})`;
                hdrStatusEl.className = 'gst-hdr-status hdr-enabled';
            } else if (!sourceIsHdr && hdrEnabled) {
                hdrStatusEl.textContent = `SDR source \u2014 10-bit pipeline active (${pipelineLabel})`;
                hdrStatusEl.className = 'gst-hdr-status hdr-enabled';
            } else if (sourceIsHdr && !hdrEnabled) {
                hdrStatusEl.textContent = `HDR source detected but HDR mode disabled - using SDR pipeline`;
                hdrStatusEl.className = 'gst-hdr-status hdr-disabled';
            } else {
                hdrStatusEl.textContent = `SDR ${colorDepth}-bit source - using standard pipeline`;
                hdrStatusEl.className = 'gst-hdr-status';
            }
        }

        if (losslessHintEl) {
            losslessHintEl.textContent = losslessEnabled && outputCodec !== 'h265'
                ? 'Lossless mode only works with H.265. Switch codec back to H.265 to use it.'
                : losslessEnabled
                ? 'Lossless mode is enabled. Use it only for 720p60 or lower because bitrate and encoder load rise sharply.'
                : 'Lossless mode is HEVC-only and is intended for archival-quality capture at 720p60 or lower.';
        }
    }

    updateTransportUI() {
        const transport = document.getElementById('auto-output-transport')?.value || 'srt';
        const codecEl = document.getElementById('auto-output-codec');
        const codecHint = document.getElementById('auto-hdr-status');
        const srtGroup = document.getElementById('auto-srt-group');
        const rtmpGroup = document.getElementById('auto-rtmp-group');
        const rtspGroup = document.getElementById('auto-rtsp-group');
        const recordingToggle = document.getElementById('auto-recording-enabled');
        const recordingPathGroup = document.getElementById('auto-recording-path-group');

        if (srtGroup) srtGroup.style.display = transport === 'srt' ? 'block' : 'none';
        if (rtmpGroup) rtmpGroup.style.display = transport === 'rtmp' ? 'block' : 'none';
        if (rtspGroup) rtspGroup.style.display = transport === 'rtsp' ? 'block' : 'none';

        if (codecEl) {
            const h265Option = Array.from(codecEl.options).find(opt => opt.value === 'h265');
            if (h265Option) {
                h265Option.disabled = transport === 'rtmp';
            }
            if (transport === 'rtmp' && codecEl.value !== 'h264') {
                codecEl.value = 'h264';
            }
        }

        if (recordingToggle) {
            if (transport !== 'srt') {
                recordingToggle.checked = false;
                recordingToggle.disabled = true;
            } else {
                recordingToggle.disabled = false;
            }
        }

        if (recordingPathGroup) {
            recordingPathGroup.style.display = transport === 'srt' && recordingToggle?.checked ? 'block' : 'none';
        }
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    window.autoConfigurator = new AutoConfigurator();
    window.autoConfigurator.init();
});
