/**
 * UVC Device Manager Component
 * 
 * Handles UVC device discovery, configuration, and pipeline generation
 * for USB Video Class devices.
 */

const UVCManager = {
    // Current state
    initialized: false,
    devices: [],
    selectedDevice: null,
    editingInstanceId: null,
    editingInstanceName: '',
    editingDevicePath: '',
    editingDeviceSerial: null,
    autostart: false,
    currentConfig: {
        format: 'auto',
        width: 1920,
        height: 1080,
        fps: 30,
        encoder: 'h265',
        bitrate: 4000,
        outputType: 'srt',
        outputConfig: {
            port: 8889,
            host: '',
            mode: 'listener'
        }
    },

    // Format options for different device capabilities
    FORMAT_OPTIONS: {
        'auto': { label: 'Auto-detect', requires: null },
        'h264': { label: 'H.264 Passthrough', requires: 'is_h264_passthrough' },
        'mjpeg': { label: 'MJPEG', requires: 'is_mjpeg' },
        'yuyv': { label: 'YUYV (Raw)', requires: 'is_yuyv' }
    },

    // Encoder options
    ENCODER_OPTIONS: [
        { value: 'h265', label: 'H.265 (amlvenc, recommended)' },
        { value: 'h264', label: 'H.264 (amlvenc)' },
        { value: 'none', label: 'No Encoding (H.264 passthrough only)' }
    ],

    // Output options
    OUTPUT_OPTIONS: [
        { value: 'srt', label: 'SRT Stream' },
        { value: 'rtmp', label: 'RTMP Stream' },
        { value: 'file', label: 'File Recording' },
        { value: 'display', label: 'Local Display' }
    ],

    /**
     * Initialize the UVC manager
     */
    init() {
        if (!this.initialized) {
            this.render();
            this.initialized = true;
        }
        this.discoverDevices();
    },

    getDefaultConfig() {
        return {
            format: 'auto',
            width: 1920,
            height: 1080,
            fps: 30,
            encoder: 'h265',
            bitrate: 4000,
            outputType: 'srt',
            outputConfig: {
                port: 8889,
                host: '',
                mode: 'listener'
            }
        };
    },

    openEditor(instance = null) {
        if (!instance) {
            this.editingInstanceId = null;
            this.editingInstanceName = '';
            this.editingDevicePath = '';
            this.editingDeviceSerial = null;
            this.autostart = false;
            this.selectedDevice = null;
            this.currentConfig = this.getDefaultConfig();
            this.renderDeviceList();
            this.renderDeviceDetails();
            return;
        }

        const cfg = instance.uvc_config || {};
        this.editingInstanceId = instance.id;
        this.editingInstanceName = instance.name || '';
        this.editingDevicePath = cfg.device_path || '';
        this.editingDeviceSerial = cfg.device_serial || null;
        this.autostart = instance.autostart || false;
        this.currentConfig = {
            format: cfg.format_type || 'auto',
            width: cfg.width || 1920,
            height: cfg.height || 1080,
            fps: cfg.fps || 30,
            encoder: cfg.encoder || 'h265',
            bitrate: Math.max(1, Math.round((cfg.bitrate || 4000000) / 1000)),
            outputType: cfg.output_type || 'srt',
            outputConfig: {
                port: cfg.output_config?.port || 8889,
                host: cfg.output_config?.host || '',
                mode: cfg.output_config?.mode || 'listener',
                url: cfg.output_config?.url || 'rtmp://localhost/live/stream',
                path: cfg.output_config?.path || '/mnt/sdcard/uvc_recording.ts'
            }
        };

        this.selectedDevice = this.devices.find((d) => d.device_path === this.editingDevicePath) || null;
        this.renderDeviceList();
        this.renderDeviceDetails();
    },

    bindEvents() {
        const container = document.getElementById('uvc-content');
        if (!container || container.dataset.bound === 'true') return;

        container.addEventListener('click', (event) => {
            const refreshBtn = event.target.closest('#uvc-refresh-btn');
            if (refreshBtn) {
                this.refreshDevices();
                return;
            }

            const createBtn = event.target.closest('#uvc-create-btn');
            if (createBtn) {
                this.createInstance();
                return;
            }

            const deviceItem = event.target.closest('.uvc-device-item');
            if (deviceItem && deviceItem.dataset.devicePath) {
                this.selectDevice(deviceItem.dataset.devicePath);
            }
        });

        container.addEventListener('change', (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement)) return;

            switch (target.id) {
            case 'uvc-format':
                this.updateConfig('format', target.value);
                break;
            case 'uvc-resolution':
                this.updateResolution(target.value);
                break;
            case 'uvc-width':
                this.updateConfig('width', parseInt(target.value, 10));
                break;
            case 'uvc-height':
                this.updateConfig('height', parseInt(target.value, 10));
                break;
            case 'uvc-fps':
                this.updateConfig('fps', parseInt(target.value, 10));
                break;
            case 'uvc-encoder':
                this.updateConfig('encoder', target.value);
                this.renderDeviceDetails();
                break;
            case 'uvc-output':
                this.updateConfig('outputType', target.value);
                this.renderDeviceDetails();
                break;
            case 'uvc-srt-port':
                this.updateConfig('output.port', parseInt(target.value, 10));
                break;
            case 'uvc-srt-mode':
                this.updateConfig('output.mode', target.value);
                this.renderOutputConfig();
                break;
            case 'uvc-srt-host':
                this.updateConfig('output.host', target.value);
                break;
            case 'uvc-rtmp-url':
                this.updateConfig('output.url', target.value);
                break;
            case 'uvc-file-path':
                this.updateConfig('output.path', target.value);
                break;
            default:
                break;
            }
        });

        container.addEventListener('input', (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement)) return;

            if (target.id === 'uvc-bitrate') {
                const value = parseInt(target.value, 10);
                this.updateConfig('bitrate', value);
                const display = document.getElementById('bitrate-display');
                if (display) {
                    display.textContent = `${value} kbps`;
                }
            }
        });

        container.dataset.bound = 'true';
    },

    /**
     * Discover UVC devices via D-Bus
     */
    async discoverDevices() {
        try {
            const result = await callMethod("GetUVCDevices");
            
            this.devices = JSON.parse(result);
            if (this.editingDevicePath) {
                this.selectedDevice = this.devices.find(d => d.device_path === this.editingDevicePath) || this.selectedDevice;
            }
            this.renderDeviceList();
            this.renderDeviceDetails();
            
            if (this.devices.length === 0) {
                this.showNotification('No UVC devices found', 'info');
            }
        } catch (error) {
            console.error('Failed to discover UVC devices:', error);
            this.showNotification('Failed to discover devices', 'error');
        }
    },

    /**
     * Refresh UVC device list
     */
    async refreshDevices() {
        const btn = document.getElementById('uvc-refresh-btn');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span> Scanning...';
        }

        try {
            const result = await callMethod("RefreshUVCDevices");
            
            this.devices = JSON.parse(result);
            if (this.editingDevicePath) {
                this.selectedDevice = this.devices.find(d => d.device_path === this.editingDevicePath) || this.selectedDevice;
            }
            this.renderDeviceList();
            this.renderDeviceDetails();
            
            if (this.devices.length > 0) {
                this.showNotification(`Found ${this.devices.length} UVC device(s)`, 'success');
            } else {
                this.showNotification('No UVC devices found', 'info');
            }
        } catch (error) {
            console.error('Failed to refresh UVC devices:', error);
            this.showNotification('Failed to refresh devices', 'error');
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = '🔄 Refresh';
            }
        }
    },

    /**
     * Select a device
     */
    selectDevice(devicePath) {
        this.selectedDevice = this.devices.find(d => d.device_path === devicePath);
        this.editingDevicePath = devicePath;
        this.renderDeviceDetails();
        this.updatePipelinePreview();
    },

    /**
     * Update configuration value
     */
    updateConfig(key, value) {
        if (key.startsWith('output.')) {
            const outputKey = key.split('.')[1];
            this.currentConfig.outputConfig[outputKey] = value;
        } else {
            this.currentConfig[key] = value;
        }
        this.updatePipelinePreview();
    },

    /**
     * Update pipeline preview
     */
    async updatePipelinePreview() {
        if (!this.selectedDevice) {
            document.getElementById('uvc-pipeline-preview').textContent = 
                'Select a device to see pipeline preview';
            return;
        }

        const outputConfig = JSON.stringify(this.currentConfig.outputConfig);
        
        try {
            const result = await callMethod(
                "GetUVCDevicePipeline",
                this.selectedDevice.device_path,
                this.currentConfig.format,
                this.currentConfig.width,
                this.currentConfig.height,
                this.currentConfig.fps,
                this.currentConfig.encoder,
                this.currentConfig.bitrate * 1000,
                this.currentConfig.outputType,
                outputConfig
            );

            if (result.startsWith('Error:')) {
                document.getElementById('uvc-pipeline-preview').textContent = result;
                document.getElementById('uvc-pipeline-preview').classList.add('error');
            } else {
                document.getElementById('uvc-pipeline-preview').textContent = result;
                document.getElementById('uvc-pipeline-preview').classList.remove('error');
            }
        } catch (error) {
            console.error('Failed to get pipeline preview:', error);
            document.getElementById('uvc-pipeline-preview').textContent = 
                'Error generating pipeline preview';
        }
    },

    /**
     * Create a new UVC instance
     */
    async createInstance() {
        if (!this.selectedDevice) {
            this.showNotification('Please select a UVC device', 'error');
            return;
        }

        const name = document.getElementById('uvc-instance-name').value;
        if (!name) {
            this.showNotification('Please enter an instance name', 'error');
            return;
        }
        
        const autostartCheckbox = document.getElementById('uvc-autostart');
        const autostart = autostartCheckbox ? autostartCheckbox.checked : false;

        const btn = document.getElementById('uvc-create-btn');
        btn.disabled = true;
        btn.textContent = this.editingInstanceId ? 'Saving...' : 'Creating...';

        try {
            const outputConfig = JSON.stringify(this.currentConfig.outputConfig);

            const result = await callMethod(
                this.editingInstanceId ? "UpdateUVCInstance" : "CreateUVCInstance",
                ...(this.editingInstanceId ? [
                    this.editingInstanceId,
                    name,
                    this.selectedDevice.device_path,
                    this.currentConfig.format,
                    this.currentConfig.width,
                    this.currentConfig.height,
                    this.currentConfig.fps,
                    this.currentConfig.encoder,
                    this.currentConfig.bitrate * 1000,
                    this.currentConfig.outputType,
                    outputConfig,
                    autostart
                ] : [
                    name,
                    this.selectedDevice.device_path,
                    this.currentConfig.format,
                    this.currentConfig.width,
                    this.currentConfig.height,
                    this.currentConfig.fps,
                    this.currentConfig.encoder,
                    this.currentConfig.bitrate * 1000,
                    this.currentConfig.outputType,
                    outputConfig,
                    autostart
                ])
            );

            const response = JSON.parse(result);
            
            if (response.error) {
                this.showNotification(`Failed: ${response.error}`, 'error');
            } else {
                const serialMsg = response.device_serial ? ` (Serial: ${response.device_serial})` : '';
                await callMethod("StartInstance", response.instance_id);
                if (typeof refreshInstances === 'function') {
                    await refreshInstances();
                }
                this.showNotification(
                    `${this.editingInstanceId ? 'Instance updated and started' : 'Instance created and started'}: ${response.instance_id}${serialMsg}`, 
                    'success'
                );
                this.editingInstanceId = response.instance_id;
                this.editingDeviceSerial = response.device_serial || null;
            }
        } catch (error) {
            console.error('Failed to create UVC instance:', error);
            this.showNotification('Failed to create instance', 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = this.editingInstanceId ? 'Save UVC Instance' : 'Create Instance';
        }
    },

    /**
     * Render the main UVC manager UI
     */
    render() {
        const container = document.getElementById('uvc-content');
        if (!container) return;

        container.innerHTML = `
            <div class="uvc-manager">
                <div class="uvc-header">
                    <h2>UVC Devices</h2>
                    <button id="uvc-refresh-btn" class="btn">
                        🔄 Refresh
                    </button>
                </div>
                
                <div class="uvc-layout">
                    <div class="uvc-device-list-container">
                        <h3>Available Devices</h3>
                        <div id="uvc-device-list" class="uvc-device-list">
                            <p class="placeholder">Click Refresh to discover UVC devices</p>
                        </div>
                    </div>
                    
                    <div class="uvc-configuration">
                        <h3>Configuration</h3>
                        <div id="uvc-device-details"></div>
                        <div id="uvc-config-form"></div>
                        <div id="uvc-pipeline-section"></div>
                    </div>
                </div>
            </div>
        `;

        this.bindEvents();
        this.renderDeviceList();
        this.renderDeviceDetails();
    },

    /**
     * Render device list
     */
    renderDeviceList() {
        const list = document.getElementById('uvc-device-list');
        if (!list) return;

        if (this.devices.length === 0) {
            list.innerHTML = '<p class="placeholder">No UVC devices found</p>';
            return;
        }

        list.innerHTML = this.devices.map(device => `
            <div class="uvc-device-item ${this.selectedDevice?.device_path === device.device_path ? 'selected' : ''}"
                 data-device-path="${this.escapeHtml(device.device_path)}">
                <div class="device-icon">📹</div>
                <div class="device-info">
                    <div class="device-name">${this.escapeHtml(device.name)}</div>
                    <div class="device-path">${device.device_path}${device.serial ? ` <span class="device-serial">(Serial: ${this.escapeHtml(device.serial)})</span>` : ''}</div>
                    <div class="device-formats">
                        ${device.is_h264_passthrough ? '<span class="badge h264">H.264</span>' : ''}
                        ${device.is_mjpeg ? '<span class="badge mjpeg">MJPEG</span>' : ''}
                        ${device.is_yuyv ? '<span class="badge yuyv">YUYV</span>' : ''}
                    </div>
                </div>
            </div>
        `).join('');
    },

    /**
     * Render device details and configuration form
     */
    renderDeviceDetails() {
        const details = document.getElementById('uvc-device-details');
        const form = document.getElementById('uvc-config-form');
        const pipelineSection = document.getElementById('uvc-pipeline-section');
        
        if (!details || !form || !pipelineSection) return;

        if (!this.selectedDevice) {
            details.innerHTML = '<p class="placeholder">Select a device to configure</p>';
            form.innerHTML = '';
            pipelineSection.innerHTML = '';
            return;
        }

        // Show device details
        const formats = this.selectedDevice.formats.map(f => 
            `<li>${this.escapeHtml(f.description)} (${f.pixelformat})</li>`
        ).join('');

        details.innerHTML = `
            <div class="device-details">
                <h4>${this.escapeHtml(this.selectedDevice.name)}</h4>
                <p><strong>Device:</strong> ${this.selectedDevice.device_path}</p>
                ${this.selectedDevice.serial ? `<p><strong>Serial:</strong> <span class="device-serial">${this.escapeHtml(this.selectedDevice.serial)}</span></p>` : ''}
                <p><strong>Bus:</strong> ${this.selectedDevice.bus_info}</p>
                <p><strong>Driver:</strong> ${this.selectedDevice.driver}</p>
                <h5>Supported Formats:</h5>
                <ul>${formats}</ul>
            </div>
        `;

        // Render configuration form
        const availableFormats = Object.entries(this.FORMAT_OPTIONS)
            .filter(([key, opt]) => {
                if (key === 'auto') return true;
                return this.selectedDevice[opt.requires];
            });

        form.innerHTML = `
            <div class="config-form">
                <div class="form-group">
                    <label>Instance Name:</label>
                    <input type="text" id="uvc-instance-name" 
                           placeholder="My UVC Camera"
                           value="${this.escapeHtml(this.editingInstanceName || (this.selectedDevice.name + ' Pipeline'))}">
                </div>
                
                ${this.selectedDevice.serial ? `
                <div class="form-group device-serial-info">
                    <label>Device Serial:</label>
                    <span class="serial-badge">${this.escapeHtml(this.selectedDevice.serial)}</span>
                    <span class="hint">Used for persistent device identification</span>
                </div>
                ` : ''}

                <div class="form-group checkbox-group">
                    <label>
                        <input type="checkbox" id="uvc-autostart" ${this.autostart ? 'checked' : ''}>
                        Auto-start when device is connected
                    </label>
                </div>

                <div class="form-group">
                    <label>Input Format:</label>
                    <select id="uvc-format">
                        ${availableFormats.map(([key, opt]) => `
                            <option value="${key}" ${this.currentConfig.format === key ? 'selected' : ''}>
                                ${opt.label}
                            </option>
                        `).join('')}
                    </select>
                </div>

                <div class="form-row">
                    <div class="form-group">
                        <label>Resolution:</label>
                        <select id="uvc-resolution">
                            <option value="1920x1080" ${this.currentConfig.width === 1920 ? 'selected' : ''}>1920x1080 (Full HD)</option>
                            <option value="1280x720" ${this.currentConfig.width === 1280 ? 'selected' : ''}>1280x720 (HD)</option>
                            <option value="640x480" ${this.currentConfig.width === 640 ? 'selected' : ''}>640x480 (VGA)</option>
                            <option value="custom">Custom</option>
                        </select>
                    </div>
                    
                     <div class="form-group" id="uvc-custom-res" style="display: ${this.currentConfig.width === 1920 || this.currentConfig.width === 1280 || this.currentConfig.width === 640 ? 'none' : 'block'}">
                        <label>Custom Resolution:</label>
                        <input type="number" id="uvc-width" value="${this.currentConfig.width}" 
                               placeholder="Width" style="width: 80px;">
                        <span>x</span>
                        <input type="number" id="uvc-height" value="${this.currentConfig.height}"
                               placeholder="Height" style="width: 80px;">
                    </div>
                </div>

                <div class="form-group">
                    <label>Framerate:</label>
                    <input type="number" id="uvc-fps" value="${this.currentConfig.fps}"
                           min="1" max="120" style="width: 80px;">
                    <span>FPS</span>
                </div>

                <div class="form-group">
                    <label>Encoder:</label>
                    <select id="uvc-encoder">
                        ${this.ENCODER_OPTIONS.map(opt => `
                            <option value="${opt.value}" ${this.currentConfig.encoder === opt.value ? 'selected' : ''}>
                                ${opt.label}
                            </option>
                        `).join('')}
                    </select>
                </div>

                <div class="form-group" id="uvc-bitrate-group" style="display: ${this.currentConfig.encoder === 'none' ? 'none' : 'block'}">
                    <label>Bitrate:</label>
                    <input type="range" id="uvc-bitrate" 
                           value="${this.currentConfig.bitrate}" min="1000" max="50000" step="1000">
                    <span id="bitrate-display">${this.currentConfig.bitrate} kbps</span>
                </div>

                <div class="form-group">
                    <label>Output:</label>
                    <select id="uvc-output">
                        ${this.OUTPUT_OPTIONS.map(opt => `
                            <option value="${opt.value}" ${this.currentConfig.outputType === opt.value ? 'selected' : ''}>
                                ${opt.label}
                            </option>
                        `).join('')}
                    </select>
                </div>

                <div id="uvc-output-config"></div>
            </div>
        `;

        this.renderOutputConfig();

        // Render pipeline preview section
        pipelineSection.innerHTML = `
            <div class="pipeline-section">
                <h4>Pipeline Preview</h4>
                <pre id="uvc-pipeline-preview" class="pipeline-preview">Select a device to see pipeline preview</pre>
                <button id="uvc-create-btn" class="btn btn-primary">
                    ${this.editingInstanceId ? 'Save UVC Instance' : 'Create Instance'}
                </button>
            </div>
        `;

        this.updatePipelinePreview();
    },

    /**
     * Render output-specific configuration
     */
    renderOutputConfig() {
        const container = document.getElementById('uvc-output-config');
        if (!container) return;

        const outputType = this.currentConfig.outputType;
        
        if (outputType === 'srt') {
            container.innerHTML = `
                <div class="form-group">
                    <label>SRT Port:</label>
                    <input type="number" id="uvc-srt-port" value="${this.currentConfig.outputConfig.port || 8889}"
                           min="1024" max="65535">
                </div>
                <div class="form-group">
                    <label>SRT Mode:</label>
                    <select id="uvc-srt-mode">
                        <option value="listener" ${this.currentConfig.outputConfig.mode === 'listener' ? 'selected' : ''}>Listener (Server)</option>
                        <option value="caller" ${this.currentConfig.outputConfig.mode === 'caller' ? 'selected' : ''}>Caller (Client)</option>
                    </select>
                </div>
                  <div class="form-group" style="display: ${this.currentConfig.outputConfig.mode === 'caller' ? 'block' : 'none'}">
                      <label>SRT Host:</label>
                      <input type="text" id="uvc-srt-host" value="${this.currentConfig.outputConfig.host || ''}"
                             placeholder="192.168.12.121">
                  </div>
              `;
        } else if (outputType === 'rtmp') {
            container.innerHTML = `
                <div class="form-group">
                    <label>RTMP URL:</label>
                    <input type="text" id="uvc-rtmp-url" value="${this.currentConfig.outputConfig.url || 'rtmp://localhost/live/stream'}"
                           placeholder="rtmp://server/live/streamkey" style="width: 100%;">
                </div>
            `;
        } else if (outputType === 'file') {
            container.innerHTML = `
                <div class="form-group">
                    <label>File Path:</label>
                    <input type="text" id="uvc-file-path" value="${this.currentConfig.outputConfig.path || '/mnt/sdcard/uvc_recording.ts'}"
                           placeholder="/path/to/recording.ts" style="width: 100%;">
                </div>
            `;
        } else {
            container.innerHTML = '';
        }
    },

    /**
     * Update resolution from dropdown
     */
    updateResolution(value) {
        if (value === 'custom') {
            document.getElementById('uvc-custom-res').style.display = 'block';
        } else {
            document.getElementById('uvc-custom-res').style.display = 'none';
            const [width, height] = value.split('x').map(Number);
            this.currentConfig.width = width;
            this.currentConfig.height = height;
            this.updatePipelinePreview();
        }
    },

    /**
     * Show notification
     */
    showNotification(message, type = 'info') {
        const notification = document.createElement('div');
        notification.className = `notification ${type}`;
        notification.textContent = message;
        
        document.body.appendChild(notification);
        
        setTimeout(() => {
            notification.remove();
        }, 3000);
    },

    /**
     * Escape HTML to prevent XSS
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
};

// Expose globally
window.UVCManager = UVCManager;
