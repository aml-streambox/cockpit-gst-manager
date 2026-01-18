/**
 * GStreamer Manager - Main JavaScript
 * 
 * Handles D-Bus communication and UI state management for the Cockpit plugin.
 */

// D-Bus configuration
const DBUS_SERVICE = "org.cockpit.GstManager";
const DBUS_PATH = "/org/cockpit/GstManager";
const DBUS_INTERFACE = "org.cockpit.GstManager1";

// Application state
const state = {
    instances: [],
    boardContext: null,
    hdmiStatus: null,
    selectedInstance: null,
    editingInstance: null,
    aiProviders: [],
    editingProvider: null,
    dbus: null
};

// Initialize the application
document.addEventListener("DOMContentLoaded", init);

async function init() {
    console.log("GStreamer Manager initializing...");

    // Connect to D-Bus
    try {
        state.dbus = cockpit.dbus(DBUS_SERVICE, { bus: "system" });
        console.log("D-Bus connection established");
    } catch (error) {
        console.error("Failed to connect to D-Bus:", error);
        showToast("Failed to connect to backend service", "error");
    }

    // Set up event handlers
    setupEventHandlers();

    // Load initial data
    await refreshAll();

    // Subscribe to D-Bus signals
    subscribeToSignals();
}

function setupEventHandlers() {
    // Header buttons
    document.getElementById("btn-refresh").addEventListener("click", refreshAll);
    document.getElementById("btn-new-instance").addEventListener("click", showNewInstanceEditor);
    document.getElementById("btn-settings").addEventListener("click", showSettings);

    // Editor buttons
    document.getElementById("btn-close-editor").addEventListener("click", hideEditor);
    document.getElementById("btn-cancel-edit").addEventListener("click", hideEditor);
    document.getElementById("btn-save-instance").addEventListener("click", saveInstance);

    // Detail view buttons
    document.getElementById("btn-close-detail").addEventListener("click", hideDetail);
    document.getElementById("btn-start-instance").addEventListener("click", startSelectedInstance);
    document.getElementById("btn-stop-instance").addEventListener("click", stopSelectedInstance);
    document.getElementById("btn-edit-instance").addEventListener("click", editSelectedInstance);
    document.getElementById("btn-delete-instance").addEventListener("click", deleteSelectedInstance);
    document.getElementById("btn-clear-logs").addEventListener("click", clearLogs);

    // Autostart toggle
    document.getElementById("detail-autostart").addEventListener("change", onAutostartChanged);
    document.getElementById("detail-trigger").addEventListener("change", onTriggerChanged);

    // Settings modal
    document.getElementById("btn-close-settings").addEventListener("click", hideSettings);
    document.getElementById("btn-add-provider").addEventListener("click", addAiProvider);
    document.getElementById("btn-cancel-provider").addEventListener("click", cancelEditProvider);
}

function subscribeToSignals() {
    if (!state.dbus) return;

    // Instance status changes
    state.dbus.subscribe(
        { interface: DBUS_INTERFACE, member: "InstanceStatusChanged" },
        (path, iface, signal, args) => {
            const [instanceId, status] = args;
            console.log(`Instance ${instanceId} status changed to ${status}`);
            onInstanceStatusChanged(instanceId, status);
        }
    );

    // HDMI signal changes
    state.dbus.subscribe(
        { interface: DBUS_INTERFACE, member: "HdmiSignalChanged" },
        (path, iface, signal, args) => {
            const [available, resolution] = args;
            console.log(`HDMI signal: ${available ? resolution : "disconnected"}`);
            onHdmiSignalChanged(available, resolution);
        }
    );
}

// Data loading functions

async function refreshAll() {
    await Promise.all([
        refreshInstances(),
        refreshBoardContext(),
        refreshHdmiStatus()
    ]);
}

async function refreshInstances() {
    try {
        const result = await callMethod("ListInstances");
        state.instances = JSON.parse(result);
        renderInstancesList();

        // Update detail view if open
        if (state.selectedInstance) {
            const updated = state.instances.find(i => i.id === state.selectedInstance.id);
            if (updated) {
                state.selectedInstance = updated;
                updateDetailView();
            }
        }
    } catch (error) {
        console.error("Failed to list instances:", error);
        showToast("Failed to load instances", "error");
    }
}

async function refreshBoardContext() {
    try {
        const result = await callMethod("GetBoardContext");
        state.boardContext = JSON.parse(result);
        renderBoardContext();
    } catch (error) {
        console.error("Failed to get board context:", error);
        document.getElementById("board-context").innerHTML =
            '<p class="gst-error-text">Failed to load hardware info</p>';
    }
}

async function refreshHdmiStatus() {
    try {
        const result = await callMethod("GetHdmiStatus");
        state.hdmiStatus = JSON.parse(result);
        renderHdmiStatus();
    } catch (error) {
        console.error("Failed to get HDMI status:", error);
    }
}

function renderHdmiStatus() {
    const dot = document.getElementById("hdmi-signal-dot");
    const text = document.getElementById("hdmi-signal-text");
    const resolution = document.getElementById("hdmi-resolution");

    if (state.hdmiStatus && state.hdmiStatus.signal_locked) {
        dot.className = "gst-signal-dot gst-signal-on";
        text.textContent = "Signal Detected";
        resolution.textContent = state.hdmiStatus.resolution ||
            `${state.hdmiStatus.width}x${state.hdmiStatus.height}@${state.hdmiStatus.fps}`;
    } else {
        dot.className = "gst-signal-dot gst-signal-off";
        text.textContent = state.hdmiStatus?.cable_connected ? "No Signal" : "Disconnected";
        resolution.textContent = "-";
    }
}

// D-Bus method calls

async function callMethod(method, ...args) {
    if (!state.dbus) {
        throw new Error("D-Bus not connected");
    }

    const proxy = state.dbus.proxy(DBUS_INTERFACE, DBUS_PATH);
    await proxy.wait();

    return await proxy[method](...args);
}

// Rendering functions

function renderInstancesList() {
    const container = document.getElementById("instances-list");

    if (state.instances.length === 0) {
        container.innerHTML = '<p class="gst-empty-state">No instances configured</p>';
        return;
    }

    container.innerHTML = state.instances.map(instance => `
        <div class="gst-instance-card ${state.selectedInstance?.id === instance.id ? "selected" : ""}" 
             data-id="${instance.id}">
            <div class="gst-instance-header">
                <span class="gst-instance-name">${escapeHtml(instance.name)}</span>
                <span class="gst-status-badge gst-status-${instance.status}">${instance.status}</span>
            </div>
            <div class="gst-instance-pipeline">${escapeHtml(truncate(instance.pipeline, 80))}</div>
        </div>
    `).join("");

    // Add click handlers
    container.querySelectorAll(".gst-instance-card").forEach(card => {
        card.addEventListener("click", () => selectInstance(card.dataset.id));
    });
}

function renderBoardContext() {
    const container = document.getElementById("board-context");

    if (!state.boardContext) {
        container.innerHTML = '<p class="gst-loading">Loading...</p>';
        return;
    }

    const ctx = state.boardContext;

    let html = '<div class="gst-hw-section">';

    // Video inputs
    html += '<h3 class="gst-hw-title">Video Inputs</h3>';
    if (ctx.video_inputs && ctx.video_inputs.length > 0) {
        html += '<ul class="gst-hw-list">';
        ctx.video_inputs.forEach(v => {
            const status = v.available ? "available" : "unavailable";
            const signal = v.current_signal ? ` (${v.current_signal})` : "";
            html += `<li class="gst-hw-item gst-hw-${status}">
                <span class="gst-hw-device">${v.device}</span>
                <span class="gst-hw-type">${v.type}${signal}</span>
            </li>`;
        });
        html += '</ul>';
    } else {
        html += '<p class="gst-hw-empty">No video inputs</p>';
    }

    // Encoders
    html += '<h3 class="gst-hw-title">Encoders</h3>';
    if (ctx.encoders && ctx.encoders.length > 0) {
        html += '<ul class="gst-hw-list">';
        ctx.encoders.forEach(e => {
            html += `<li class="gst-hw-item gst-hw-available">${e}</li>`;
        });
        html += '</ul>';
    } else {
        html += '<p class="gst-hw-empty">No encoders available</p>';
    }

    // Storage
    html += '<h3 class="gst-hw-title">Storage</h3>';
    if (ctx.storage && ctx.storage.length > 0) {
        html += '<ul class="gst-hw-list">';
        ctx.storage.forEach(s => {
            if (s.available) {
                html += `<li class="gst-hw-item gst-hw-available">
                    <span class="gst-hw-device">${s.path}</span>
                    <span class="gst-hw-info">${s.free_gb}GB free</span>
                </li>`;
            }
        });
        html += '</ul>';
    }

    html += '</div>';
    container.innerHTML = html;
}

function updateDetailView() {
    const inst = state.selectedInstance;
    if (!inst) return;

    document.getElementById("detail-title").textContent = inst.name;
    document.getElementById("detail-status").textContent = inst.status;
    document.getElementById("detail-status").className = `gst-status-badge gst-status-${inst.status}`;
    document.getElementById("detail-pid").textContent = inst.pid || "-";
    document.getElementById("detail-error").textContent = inst.error_message || "-";
    document.getElementById("detail-pipeline").textContent = inst.pipeline;

    // Update button states
    const isRunning = inst.status === "running";
    document.getElementById("btn-start-instance").disabled = isRunning;
    document.getElementById("btn-stop-instance").disabled = !isRunning;
    document.getElementById("btn-edit-instance").disabled = isRunning;
    document.getElementById("btn-delete-instance").disabled = isRunning;

    // Fetch uptime
    if (isRunning) {
        callMethod("GetInstanceStatus", inst.id).then(result => {
            const status = JSON.parse(result);
            document.getElementById("detail-uptime").textContent =
                status.uptime ? formatUptime(status.uptime) : "-";

            // Show logs if available
            if (status.has_logs) {
                loadLogs();
            }
        }).catch(() => { });
    } else {
        document.getElementById("detail-uptime").textContent = "-";
    }

    // Update autostart controls
    const autostartCheckbox = document.getElementById("detail-autostart");
    const triggerRow = document.getElementById("trigger-row");
    const triggerSelect = document.getElementById("detail-trigger");

    autostartCheckbox.checked = inst.autostart || false;
    triggerRow.style.display = inst.autostart ? "flex" : "none";
    triggerSelect.value = inst.trigger_event || "";
}

// Instance actions

function selectInstance(instanceId) {
    state.selectedInstance = state.instances.find(i => i.id === instanceId);
    if (state.selectedInstance) {
        hideEditor();
        updateDetailView();
        document.getElementById("instance-detail").style.display = "block";
        renderInstancesList();  // Update selection highlight
    }
}

function showNewInstanceEditor() {
    state.editingInstance = null;
    document.getElementById("editor-title").textContent = "New Instance";
    document.getElementById("instance-name").value = "";
    document.getElementById("instance-pipeline").value = "";
    hideDetail();
    document.getElementById("pipeline-editor").style.display = "block";
}

function editSelectedInstance() {
    if (!state.selectedInstance) return;

    state.editingInstance = state.selectedInstance;
    document.getElementById("editor-title").textContent = "Edit Instance";
    document.getElementById("instance-name").value = state.selectedInstance.name;
    document.getElementById("instance-pipeline").value = state.selectedInstance.pipeline;
    hideDetail();
    document.getElementById("pipeline-editor").style.display = "block";
}

async function saveInstance() {
    const name = document.getElementById("instance-name").value.trim();
    const pipeline = document.getElementById("instance-pipeline").value.trim();

    if (!name) {
        showToast("Please enter a name", "warning");
        return;
    }
    if (!pipeline) {
        showToast("Please enter a pipeline command", "warning");
        return;
    }

    try {
        if (state.editingInstance) {
            // Update existing
            await callMethod("UpdatePipeline", state.editingInstance.id, pipeline);
            showToast("Instance updated successfully", "success");
        } else {
            // Create new
            const newId = await callMethod("CreateInstance", name, pipeline);
            showToast(`Instance created: ${newId}`, "success");
        }

        hideEditor();
        await refreshInstances();
    } catch (error) {
        console.error("Failed to save instance:", error);
        showToast("Failed to save instance: " + error.message, "error");
    }
}

async function startSelectedInstance() {
    if (!state.selectedInstance) return;

    try {
        await callMethod("StartInstance", state.selectedInstance.id);
        showToast("Instance started", "success");
        await refreshInstances();
    } catch (error) {
        console.error("Failed to start instance:", error);
        showToast("Failed to start: " + error.message, "error");
    }
}

async function stopSelectedInstance() {
    if (!state.selectedInstance) return;

    try {
        await callMethod("StopInstance", state.selectedInstance.id);
        showToast("Instance stopped", "success");
        await refreshInstances();
    } catch (error) {
        console.error("Failed to stop instance:", error);
        showToast("Failed to stop: " + error.message, "error");
    }
}

async function deleteSelectedInstance() {
    if (!state.selectedInstance) return;

    if (!confirm(`Delete instance "${state.selectedInstance.name}"?`)) {
        return;
    }

    try {
        await callMethod("DeleteInstance", state.selectedInstance.id);
        showToast("Instance deleted", "success");
        state.selectedInstance = null;
        hideDetail();
        await refreshInstances();
    } catch (error) {
        console.error("Failed to delete instance:", error);
        showToast("Failed to delete: " + error.message, "error");
    }
}

async function loadLogs() {
    if (!state.selectedInstance) return;

    try {
        const result = await callMethod("GetInstanceLogs", state.selectedInstance.id, 50);
        const logs = JSON.parse(result);

        const logsPanel = document.getElementById("logs-panel");
        const logsContent = document.getElementById("detail-logs");

        if (logs.length > 0) {
            logsPanel.style.display = "block";
            logsContent.textContent = logs.join("\n");
        } else {
            logsPanel.style.display = "none";
        }
    } catch (error) {
        console.error("Failed to load logs:", error);
    }
}

async function clearLogs() {
    if (!state.selectedInstance) return;

    try {
        await callMethod("ClearInstanceLogs", state.selectedInstance.id);
        document.getElementById("logs-panel").style.display = "none";
        document.getElementById("detail-logs").textContent = "";
        showToast("Logs cleared", "success");
    } catch (error) {
        console.error("Failed to clear logs:", error);
        showToast("Failed to clear logs", "error");
    }
}

// Signal handlers

function onInstanceStatusChanged(instanceId, status) {
    const instance = state.instances.find(i => i.id === instanceId);
    if (instance) {
        instance.status = status;
        renderInstancesList();

        if (state.selectedInstance?.id === instanceId) {
            state.selectedInstance.status = status;
            updateDetailView();
        }
    }
}

function onHdmiSignalChanged(available, resolution) {
    state.hdmiStatus = {
        signal_locked: available,
        resolution: resolution
    };
    renderHdmiStatus();
}

async function onAutostartChanged() {
    if (!state.selectedInstance) return;

    const enabled = document.getElementById("detail-autostart").checked;
    const trigger = document.getElementById("detail-trigger").value;

    document.getElementById("trigger-row").style.display = enabled ? "flex" : "none";

    try {
        await callMethod("SetInstanceAutostart", state.selectedInstance.id, enabled, trigger);
        state.selectedInstance.autostart = enabled;
        showToast(`Autostart ${enabled ? "enabled" : "disabled"}`, "success");
    } catch (error) {
        console.error("Failed to set autostart:", error);
        showToast("Failed to update autostart", "error");
    }
}

async function onTriggerChanged() {
    if (!state.selectedInstance || !state.selectedInstance.autostart) return;

    const trigger = document.getElementById("detail-trigger").value;

    try {
        await callMethod("SetInstanceAutostart", state.selectedInstance.id, true, trigger);
        state.selectedInstance.trigger_event = trigger || null;
        showToast("Trigger updated", "success");
    } catch (error) {
        console.error("Failed to set trigger:", error);
        showToast("Failed to update trigger", "error");
    }
}

// UI helpers

function hideEditor() {
    document.getElementById("pipeline-editor").style.display = "none";
    state.editingInstance = null;
}

function hideDetail() {
    document.getElementById("instance-detail").style.display = "none";
}

function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    const toast = document.createElement("div");
    toast.className = `gst-toast gst-toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add("gst-toast-fade");
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function truncate(text, maxLength) {
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + "...";
}

function formatUptime(seconds) {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    const hours = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${mins}m`;
}

// Settings functions

async function showSettings() {
    document.getElementById("settings-modal").style.display = "flex";
    await loadAiProviders();
}

function hideSettings() {
    document.getElementById("settings-modal").style.display = "none";
}

async function loadAiProviders() {
    try {
        const result = await callMethod("GetAiProviders");
        const providers = JSON.parse(result);
        renderProvidersList(providers);
    } catch (error) {
        console.error("Failed to load providers:", error);
    }
}

function renderProvidersList(providers) {
    const container = document.getElementById("ai-providers-list");

    // Store providers for editing
    state.aiProviders = providers;

    if (!providers || providers.length === 0) {
        container.innerHTML = '<p class="gst-text-muted">No AI providers configured.</p>';
        return;
    }

    let html = '';
    for (const p of providers) {
        const keyStatus = p.has_key ? 'Key set' : 'No key';
        html += `
            <div class="gst-provider-item">
                <div class="gst-provider-info">
                    <strong>${escapeHtml(p.name)}</strong>
                    <span class="gst-text-muted">${escapeHtml(p.model || '')} - ${keyStatus}</span>
                </div>
                <div class="gst-provider-actions">
                    <button class="gst-btn gst-btn-secondary gst-btn-sm btn-edit-provider" 
                            data-name="${escapeHtml(p.name)}">Edit</button>
                    <button class="gst-btn gst-btn-danger gst-btn-sm btn-remove-provider" 
                            data-name="${escapeHtml(p.name)}">Remove</button>
                </div>
            </div>
        `;
    }
    container.innerHTML = html;

    // Add event listeners (CSP-compliant)
    container.querySelectorAll('.btn-edit-provider').forEach(btn => {
        btn.addEventListener('click', () => editAiProvider(btn.dataset.name));
    });
    container.querySelectorAll('.btn-remove-provider').forEach(btn => {
        btn.addEventListener('click', () => removeAiProvider(btn.dataset.name));
    });
}

async function addAiProvider() {
    const name = document.getElementById("provider-name").value.trim();
    const url = document.getElementById("provider-url").value.trim();
    const model = document.getElementById("provider-model").value.trim();
    const key = document.getElementById("provider-key").value.trim();
    const isEditing = !!state.editingProvider;

    // For new providers, all fields required. For editing, key is optional
    if (!name || !url || !model) {
        showToast("Name, URL, and Model are required", "error");
        return;
    }

    if (!isEditing && !key) {
        showToast("API Key is required for new providers", "error");
        return;
    }

    try {
        // If editing and name changed, remove old provider first
        if (isEditing && state.editingProvider !== name) {
            await callMethod("RemoveAiProvider", state.editingProvider);
        }

        // Use __KEEP__ marker if editing without new key
        const actualKey = key || (isEditing ? "__KEEP__" : "");

        const success = await callMethod("AddAiProvider", name, url, actualKey, model);
        if (success) {
            showToast(isEditing ? "Provider updated" : "Provider added", "success");
            cancelEditProvider(); // Clear form and reset state
            await loadAiProviders();
        } else {
            showToast("Failed to save provider", "error");
        }
    } catch (error) {
        console.error("Failed to save provider:", error);
        showToast("Failed to save provider", "error");
    }
}

async function removeAiProvider(name) {
    if (!confirm(`Remove AI provider "${name}"?`)) {
        return;
    }

    try {
        const success = await callMethod("RemoveAiProvider", name);
        if (success) {
            showToast("Provider removed", "success");
            await loadAiProviders();
        } else {
            showToast("Failed to remove provider", "error");
        }
    } catch (error) {
        console.error("Failed to remove provider:", error);
        showToast("Failed to remove provider", "error");
    }
}

function editAiProvider(name) {
    // Find provider in stored list
    const provider = (state.aiProviders || []).find(p => p.name === name);
    if (!provider) {
        showToast("Provider not found", "error");
        return;
    }

    // Populate form
    document.getElementById("provider-name").value = provider.name || "";
    document.getElementById("provider-url").value = provider.url || "";
    document.getElementById("provider-model").value = provider.model || "";
    document.getElementById("provider-key").value = ""; // Don't show existing key
    document.getElementById("provider-key").placeholder = "Enter new key (leave blank to keep current)";

    // Mark as editing
    state.editingProvider = name;

    // Change button text and show cancel
    document.getElementById("btn-add-provider").textContent = "Update Provider";
    document.getElementById("btn-cancel-provider").style.display = "inline-block";

    showToast("Edit provider details and click Update", "info");
}

function cancelEditProvider() {
    state.editingProvider = null;
    document.getElementById("provider-name").value = "";
    document.getElementById("provider-url").value = "";
    document.getElementById("provider-model").value = "";
    document.getElementById("provider-key").value = "";
    document.getElementById("provider-key").placeholder = "Your API key";
    document.getElementById("btn-add-provider").textContent = "Add Provider";
    document.getElementById("btn-cancel-provider").style.display = "none";
}
