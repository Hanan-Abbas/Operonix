/**
 * Operonix Dashboard — app.js
 *
 * Central application controller. Owns:
 *   - Global configuration (API base, WS URL)
 *   - WebSocket lifecycle (connect / reconnect / heartbeat / dispatch)
 *   - Theme management (dark/light, persisted to localStorage)
 *   - Navigation / view routing
 *   - Input mode switching (voice | panel | none)
 *   - Safety confirmation modal
 *   - Toast notification system
 *   - Shared system actions (reflect, remap, shutdown)
 *   - Uptime ticker
 *   - Health polling
 *
 * Component JS files (system_health.js, action_stream.js, etc.) register
 * themselves via App.registerComponent() and receive WS events through
 * App.ws.on(eventType, handler).
 *
 * Mode switching
 * ──────────────
 * inputMode.activate(mode) calls POST /api/system/input-mode which delegates
 * to ModeManager on the Python side. ModeManager waits for any active task,
 * tears down the old subsystem, starts the new one, persists to .env, and
 * publishes input_mode_changed on the EventBus.
 *
 * The WebSocket bridge (api/websocket.py) forwards input_mode_changed to all
 * connected dashboard clients automatically (it subscribes to "*").
 * _init() registers a ws.on("input_mode_changed") listener so every open
 * browser tab reflects the new state without polling.
 *
 * The ModeSwitcher component (dashboard/components/mode_switcher.js) uses the
 * same wsClient.onEvent() API and is mounted into #modeSwitcherMount if the
 * element exists in index.html.
 */

"use strict";

// ═══════════════════════════════════════════════════════════════════════════
// CONFIGURATION
// ═══════════════════════════════════════════════════════════════════════════

const CONFIG = Object.freeze({
  API_BASE:       "http://localhost:8000",
  WS_URL:         "ws://localhost:8000/ws/dashboard",

  // Polling intervals (ms)
  HEALTH_POLL_MS: 5_000,
  UPTIME_TICK_MS: 1_000,

  // WS reconnect
  WS_RECONNECT_INITIAL_MS: 2_000,
  WS_RECONNECT_MAX_MS:     30_000,
  WS_HEARTBEAT_MS:         25_000,

  // Safety confirm auto-deny (ms)
  CONFIRM_TIMEOUT_MS: 30_000,

  // Toast auto-dismiss (ms)
  TOAST_DURATION_MS: 4_500,
});

// ═══════════════════════════════════════════════════════════════════════════
// UTILITIES
// ═══════════════════════════════════════════════════════════════════════════

const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

/**
 * Lightweight fetch wrapper. Always resolves — returns { ok, data, error }.
 * Throws are caught so callers never need try/catch.
 */
async function apiFetch(path, options = {}) {
  const url = CONFIG.API_BASE + path;
  try {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      return { ok: false, data: null, error: data?.detail || `HTTP ${res.status}` };
    }
    return { ok: true, data, error: null };
  } catch (err) {
    return { ok: false, data: null, error: err.message };
  }
}

/** Format seconds → HH:MM:SS */
function fmtUptime(secs) {
  const h = String(Math.floor(secs / 3600)).padStart(2, "0");
  const m = String(Math.floor((secs % 3600) / 60)).padStart(2, "0");
  const s = String(Math.floor(secs % 60)).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

/** Format ISO timestamp → HH:MM:SS */
function fmtTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleTimeString("en-US", { hour12: false }); }
  catch { return iso; }
}

/** Escape HTML to prevent XSS */
function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Action type → CSS chip class */
function actionChipClass(type = "") {
  const t = type.toLowerCase();
  if (t.includes("file") || t.includes("text"))    return "chip--file";
  if (t.includes("shell") || t.includes("command")) return "chip--shell";
  if (t.includes("web")  || t.includes("search"))   return "chip--web";
  if (t.includes("ui")   || t.includes("click"))    return "chip--ui";
  if (t.includes("api")  || t.includes("http"))     return "chip--api";
  return "chip--muted";
}

// ═══════════════════════════════════════════════════════════════════════════
// MAIN APPLICATION OBJECT
// ═══════════════════════════════════════════════════════════════════════════

const App = (() => {

  // ── State ────────────────────────────────────────────────────────────────
  let _startupTime     = Date.now();       // set properly from /api/health
  let _uptimeSecs      = 0;
  let _uptimeInterval  = null;
  let _healthInterval  = null;
  let _currentView     = "overview";
  let _currentMode     = "none";           // "voice" | "panel" | "none"
  let _confirmCallback = null;
  let _confirmTimer    = null;
  let _components      = {};              // name → {mount, onEvent}

  // ── Confirmation state ───────────────────────────────────────────────────
  // Stores the task_id from the most recent confirmation_required event so
  // safety.respond() can include it in the dashboard_command message.
  // websocket.py uses it to emit the correct user_response_received event.
  let _pendingConfirmTaskId = null;

  // ── WS internal ──────────────────────────────────────────────────────────
  let _ws              = null;
  let _wsReady         = false;
  let _wsListeners     = {};              // eventType → [fn]
  let _wsReconnectMs   = CONFIG.WS_RECONNECT_INITIAL_MS;
  let _wsReconnectTimer = null;
  let _wsHeartbeatTimer = null;


  // ═══════════════════════════════════════════════════════════════════════
  // THEME
  // ═══════════════════════════════════════════════════════════════════════

  const theme = {
    _current: "dark",

    init() {
      const saved = localStorage.getItem("operonix-theme") || "dark";
      theme.apply(saved);
    },

    toggle() {
      theme.apply(theme._current === "dark" ? "light" : "dark");
    },

    apply(name) {
      theme._current = name;
      document.documentElement.setAttribute("data-theme", name);
      localStorage.setItem("operonix-theme", name);
      // Swap icon
      const moon = $(".icon-moon");
      const sun  = $(".icon-sun");
      if (moon) moon.style.display = name === "dark"  ? ""     : "none";
      if (sun)  sun.style.display  = name === "light" ? ""     : "none";
    },
  };


  // ═══════════════════════════════════════════════════════════════════════
  // WEBSOCKET
  // ═══════════════════════════════════════════════════════════════════════

  const ws = {
    /** Register a listener for a specific event_type from the bus. */
    on(eventType, fn) {
      if (!_wsListeners[eventType]) _wsListeners[eventType] = [];
      _wsListeners[eventType].push(fn);
    },

    /** Remove a listener. */
    off(eventType, fn) {
      const arr = _wsListeners[eventType];
      if (arr) {
        _wsListeners[eventType] = arr.filter(f => f !== fn);
      }
    },

    /** Send a JSON message if the socket is open. */
    send(payload) {
      if (_ws && _ws.readyState === WebSocket.OPEN) {
        _ws.send(JSON.stringify(payload));
        return true;
      }
      return false;
    },

    /** Subscribe to specific event channels. */
    subscribe(channels) {
      ws.send({ action: "SUBSCRIBE", channels });
    },

    reconnect() {
      if (_ws) { _ws.onclose = null; _ws.close(); }
      _connect();
    },

    /**
     * Register a typed event listener — returns an unsubscribe function.
     * Used by ModeSwitcher and other components that need clean teardown.
     *
     * @param {string} eventType
     * @param {Function} fn
     * @returns {Function} unsubscribe
     */
    onEvent(eventType, fn) {
      ws.on(eventType, fn);
      return () => ws.off(eventType, fn);
    },
  };

  function _connect() {
    clearTimeout(_wsReconnectTimer);
    clearInterval(_wsHeartbeatTimer);
    _setWsState("connecting");

    try {
      _ws = new WebSocket(CONFIG.WS_URL);
    } catch (e) {
      _scheduleReconnect();
      return;
    }

    _ws.onopen = () => {
      _wsReady = true;
      _wsReconnectMs = CONFIG.WS_RECONNECT_INITIAL_MS;
      _setWsState("connected");
      // Subscribe to all channels
      ws.send({ action: "SUBSCRIBE", channels: [] });
      // Start heartbeat
      _wsHeartbeatTimer = setInterval(() => {
        ws.send({ action: "PING" });
      }, CONFIG.WS_HEARTBEAT_MS);
    };

    _ws.onmessage = (evt) => {
      let msg;
      try { msg = JSON.parse(evt.data); } catch { return; }
      _dispatch(msg);
    };

    _ws.onclose = (evt) => {
      _wsReady = false;
      clearInterval(_wsHeartbeatTimer);
      _setWsState("disconnected");
      // Don't reconnect on intentional close (code 1000)
      if (evt.code !== 1000) _scheduleReconnect();
    };

    _ws.onerror = () => {
      // onclose fires after onerror — reconnect handled there
    };
  }

  function _dispatch(msg) {
    // pong — ignore
    if (msg.type === "pong") return;

    // ack — ignore for now
    if (msg.type === "ack") return;

    // Routed event from the EventBus
    if (msg.type === "event") {
      const etype = msg.event_type;

      // Safety confirmation intercept
      if (etype === "confirmation_required") {
        // Plugin approval requests have type:"plugin_approval" — they need
        // APPROVE_PLUGIN / REJECT_PLUGIN WS actions which publish
        // plugin_approved / plugin_rejected on the EventBus for the generator
        // to handle.  Routing them through confirm_approved/confirm_denied
        // would send the response to ConfirmationManager (wrong handler) and
        // the plugin would never be deployed to its target directory.
        if (msg.data?.type === "plugin_approval") {
          pluginApproval.show(msg.data);
          return;
        }
        // All other confirmation_required → standard safety modal
        _pendingConfirmTaskId = msg.data?.task_id || null;
        safety.show(msg.data);
        return;
      }

      // Plugin lifecycle events — keep approval panel in sync
      if (["plugin_approved","plugin_rejected","plugin_installed",
           "plugin_generation_failed","plugin_validation_failed"].includes(etype)) {
        pluginApproval.onLifecycleEvent(etype, msg.data);
      }

      // Dispatch to all registered listeners
      const listeners = [
        ...(_wsListeners[etype] || []),
        ...(_wsListeners["*"]   || []),
      ];
      listeners.forEach(fn => {
        try { fn(msg.data, msg); } catch (err) { console.warn("[App.ws] listener error:", err); }
      });
    }
  }

  function _scheduleReconnect() {
    _wsReconnectTimer = setTimeout(() => {
      _connect();
    }, _wsReconnectMs);
    // Exponential back-off, cap at max
    _wsReconnectMs = Math.min(_wsReconnectMs * 1.5, CONFIG.WS_RECONNECT_MAX_MS);
  }

  function _setWsState(state) {
    const bar  = $("#wsStatusBar");
    const dot  = $("#wsDot");
    const text = $("#wsStatusText");
    const btn  = $("#wsReconnectBtn");
    if (!bar) return;

    bar.className = `ws-statusbar ${state === "connected" ? "connected" : state === "disconnected" ? "disconnected" : ""}`;
    dot.style.animationPlayState = state === "connected" ? "running" : "paused";

    if (state === "connecting") {
      text.textContent = `Connecting to ${CONFIG.WS_URL}…`;
      btn.style.display = "none";
    } else if (state === "connected") {
      text.textContent = `Connected · ${CONFIG.WS_URL} · EventBus bridged`;
      btn.style.display = "none";
    } else {
      text.textContent = `Disconnected from ${CONFIG.WS_URL} — retrying in ${(_wsReconnectMs / 1000).toFixed(0)}s`;
      btn.style.display = "block";
    }
  }


  // ═══════════════════════════════════════════════════════════════════════
  // NAVIGATION
  // ═══════════════════════════════════════════════════════════════════════

  const VIEW_TITLES = {
    overview:  "Overview",
    actions:   "Action Stream",
    decisions: "Decision View",
    logs:      "Live Logs",
    plugins:   "Plugin Manager",
    evolution: "Self-Evolution",
    memory:    "Memory",
    settings:  "Settings",
  };

  function navigate(viewId, navEl) {
    if (viewId === _currentView) return;
    _currentView = viewId;

    // Update nav items
    $$(".nav-item").forEach(el => el.classList.remove("active"));
    if (navEl) navEl.classList.add("active");

    // Switch views
    $$(".view").forEach(v => v.classList.remove("active"));
    const target = $(`#view-${viewId}`);
    if (target) target.classList.add("active");

    // Update topbar title
    const titleEl = $("#topbarTitle");
    if (titleEl) titleEl.textContent = VIEW_TITLES[viewId] || viewId;
  }


  // ═══════════════════════════════════════════════════════════════════════
  // INPUT MODE
  // ═══════════════════════════════════════════════════════════════════════

  const inputMode = {
    _pending: false,

    /**
     * Request a mode change via the API.
     * ModeManager on the Python side handles task drain, teardown, startup,
     * .env persistence, and publishing input_mode_changed on the EventBus.
     * The ws.on("input_mode_changed") listener below then calls _render()
     * so every open tab reflects the new state automatically.
     */
    async activate(mode) {
      if (inputMode._pending) return;
      if (mode === _currentMode) return;

      inputMode._pending = true;
      const prev = _currentMode;

      // Show pending state visually while we wait for ModeManager to drain
      // any active task (can take up to MODE_SWITCH_DRAIN_TIMEOUT seconds).
      inputMode._renderPending(mode);

      const { ok, data, error } = await apiFetch("/api/system/input-mode", {
        method: "POST",
        body: JSON.stringify({ mode }),
      });

      inputMode._pending = false;

      if (!ok) {
        // Rollback to previous state
        _currentMode = prev;
        inputMode._render(prev);
        toast.show("error", "Mode Switch Failed", error || "Could not change input mode.");
      } else {
        // data.mode is the confirmed new mode from the server.
        // _render() will also be called by the ws input_mode_changed listener
        // below — calling it here too is harmless (idempotent).
        _currentMode = data.mode || mode;
        inputMode._render(_currentMode);
        const label = _currentMode === "none"
          ? "All modes deactivated"
          : `${_currentMode.charAt(0).toUpperCase() + _currentMode.slice(1)} mode activated`;
        toast.show("success", "Input Mode", label);
      }
    },

    /**
     * Apply a mode change that arrived from the server (WebSocket event).
     * Does NOT call the API — the change already happened server-side.
     */
    applyFromServer(mode) {
      if (mode === _currentMode) return;
      _currentMode = mode;
      inputMode._render(mode);
    },

    _render(mode) {
      const voiceCard  = $("#modeCardVoice");
      const panelCard  = $("#modeCardPanel");
      const voiceState = $("#voiceState");
      const panelState = $("#panelState");
      const voiceWave  = $("#voiceWaveform");
      const noneBtn    = $("#modeNoneBtn");

      if (!voiceCard) return;

      // Reset
      voiceCard.classList.remove("active", "pending");
      panelCard.classList.remove("active", "pending");
      voiceCard.setAttribute("aria-pressed", "false");
      panelCard.setAttribute("aria-pressed", "false");
      if (voiceWave) voiceWave.classList.remove("listening");

      if (mode === "voice") {
        voiceCard.classList.add("active");
        voiceCard.setAttribute("aria-pressed", "true");
        if (voiceState) voiceState.textContent = "Active";
        if (panelState) panelState.textContent = "Inactive";
        if (voiceWave)  voiceWave.classList.add("listening");
        if (noneBtn)    noneBtn.style.display = "";
      } else if (mode === "panel") {
        panelCard.classList.add("active");
        panelCard.setAttribute("aria-pressed", "true");
        if (voiceState) voiceState.textContent = "Inactive";
        if (panelState) panelState.textContent = "Active";
        if (noneBtn)    noneBtn.style.display = "";
      } else {
        if (voiceState) voiceState.textContent = "Inactive";
        if (panelState) panelState.textContent = "Inactive";
        if (noneBtn)    noneBtn.style.display = "none";
      }
    },

    /**
     * Show a "waiting" visual state while ModeManager drains the active task.
     * Adds a .pending class so CSS can show a spinner or muted colour.
     */
    _renderPending(targetMode) {
      const voiceCard = $("#modeCardVoice");
      const panelCard = $("#modeCardPanel");
      if (!voiceCard) return;
      voiceCard.classList.remove("active");
      panelCard.classList.remove("active");
      if (targetMode === "voice") voiceCard.classList.add("pending");
      if (targetMode === "panel") panelCard.classList.add("pending");
    },

    /** Update the voice RMS display from audio health data */
    updateRms(rms) {
      const el = $("#voiceRms");
      if (el) el.textContent = `RMS ${typeof rms === "number" ? rms.toFixed(2) : "—"}`;
    },

    /** Update panel snippet count */
    updateSnippets(count) {
      const el = $("#panelSnippets");
      if (el) el.textContent = `${count} snippets`;
    },
  };


  // ═══════════════════════════════════════════════════════════════════════
  // HEALTH POLLING
  // ═══════════════════════════════════════════════════════════════════════

  const health = {
    _data: null,

    async poll() {
      const { ok, data } = await apiFetch("/api/health");
      if (!ok || !data) return;
      health._data = data;
      health._apply(data);
    },

    async pollDetailed() {
      const { ok, data } = await apiFetch("/api/health/detailed");
      if (!ok || !data) return;
      // Broadcast to all WS listeners registered for "health_update"
      const listeners = _wsListeners["health_update"] || [];
      listeners.forEach(fn => {
        try { fn(data); } catch (e) { console.warn(e); }
      });
    },

    _apply(data) {
      const dot  = $("#statusDot");
      const text = $("#statusText");
      const upt  = $("#topbarUptime");
      const env  = $("#topbarEnv");

      const s = data.status || "unknown";

      if (dot) {
        dot.className = `status-dot ${s}`;
      }
      if (text) {
        const label = s === "healthy" ? "Agent healthy"
          : s === "degraded" ? "Agent degraded"
          : "Agent unhealthy";
        text.textContent = label;
      }

      // Uptime (set once from server, then tick locally)
      if (data.uptime_seconds != null && _uptimeSecs === 0) {
        _uptimeSecs = Math.floor(data.uptime_seconds);
        _startupTime = Date.now() - _uptimeSecs * 1000;
      }

      if (env && data.environment) env.textContent = data.environment;

      // Update version from system info if available
      if (data.version) {
        const ver = $(".logo-version");
        if (ver) ver.textContent = `v${data.version}`;
      }
    },
  };


  // ═══════════════════════════════════════════════════════════════════════
  // SYSTEM ACTIONS
  // ═══════════════════════════════════════════════════════════════════════

  const system = {
    async triggerReflect() {
      const btn = $("#btnReflect");
      if (btn) btn.classList.add("loading");
      const { ok, error } = await apiFetch("/api/system/evolve/reflect", { method: "POST" });
      if (btn) btn.classList.remove("loading");
      if (ok) {
        toast.show("info", "Reflector", "Reflection triggered — waiting for analysis…");
      } else {
        toast.show("error", "Reflector Failed", error);
      }
    },

    async remapCapabilities() {
      const btn = $("#btnRemap");
      if (btn) btn.classList.add("loading");
      const { ok, error } = await apiFetch("/api/system/evolve/remap-capabilities", { method: "POST" });
      if (btn) btn.classList.remove("loading");
      if (ok) {
        toast.show("info", "Capability Mapper", "Re-mapping capabilities…");
      } else {
        toast.show("error", "Remap Failed", error);
      }
    },

    async requestShutdown() {
      // Use the safety modal for this destructive action
      safety.show({
        action:    "system_shutdown",
        source:    "dashboard",
        task_id:   "—",
        risk:      "high",
        command:   "lifecycle_manager.shutdown()\n→ flushes patterns → prunes memory → stops all components",
        title:     "Shutdown Agent",
        subtitle:  "This will gracefully stop all components.",
      }, async (approved) => {
        if (!approved) return;
        const { ok, error } = await apiFetch("/api/system/shutdown", { method: "POST" });
        if (ok) {
          toast.show("warn", "Shutdown", "Graceful shutdown requested.");
        } else {
          toast.show("error", "Shutdown Failed", error);
        }
      });
    },
  };



  // ═══════════════════════════════════════════════════════════════════════
  // PLUGIN APPROVAL
  // ═══════════════════════════════════════════════════════════════════════
  //
  // Handles confirmation_required { type:"plugin_approval" } events emitted
  // by generator.py for medium/high-risk plugins.
  //
  // Flow:
  //   generator.py  → bus.publish("confirmation_required", {type:"plugin_approval",...})
  //   websocket.py  → forwards to dashboard as WS event
  //   _dispatch()   → routes to pluginApproval.show()   (NOT safety modal)
  //   User clicks   → ws.send({action:"APPROVE_PLUGIN"|"REJECT_PLUGIN", name,...})
  //   websocket.py  → bus.publish("plugin_approved"|"plugin_rejected",...)
  //   generator.py  → _on_plugin_approved() → deploys plugin to plugins/installed/
  //
  // Plugins queue up — if multiple arrive while the user is away, none are
  // lost. They can be reviewed one by one.

  const pluginApproval = (() => {
    const _queue = [];   // pending approval items
    let _currentIdx = 0;
    let _panelVisible = false;

    // ── DOM helpers ─────────────────────────────────────────────────────

    function _getPanel()   { return document.getElementById("pluginApprovalPanel"); }
    function _getBackdrop(){ return document.getElementById("pluginApprovalBackdrop"); }
    function _getBadge()   { return document.getElementById("pluginApprovalBadge"); }

    function _updateBadge() {
      const badge = _getBadge();
      if (!badge) return;
      badge.textContent = _queue.length;
      badge.style.display = _queue.length > 0 ? "" : "none";
    }

    // ── CSS (injected once) ──────────────────────────────────────────────

    function _injectCSS() {
      if (document.getElementById("pluginApprovalCSS")) return;
      const style = document.createElement("style");
      style.id = "pluginApprovalCSS";
      style.textContent = `
        .pap-backdrop {
          position:fixed; inset:0; background:rgba(0,0,0,.55); z-index:9998;
        }
        .pap-backdrop.pap-hidden { display:none; }
        .plugin-approval-panel {
          position:fixed; top:50%; left:50%;
          transform:translate(-50%,-50%);
          z-index:9999;
          width:min(520px,92vw); max-height:82vh;
          display:flex; flex-direction:column;
          background:var(--color-background-primary,#1a1a2e);
          border:1px solid var(--color-border-secondary,#334);
          border-radius:12px;
          box-shadow:0 24px 64px rgba(0,0,0,.65);
          overflow:hidden; font-family:inherit;
        }
        .plugin-approval-panel.pap-hidden { display:none; }
        .pap-header {
          display:flex; align-items:center; justify-content:space-between;
          padding:14px 16px;
          background:var(--color-background-secondary,#16213e);
          border-bottom:1px solid var(--color-border-tertiary,#223);
        }
        .pap-title {
          display:flex; align-items:center; gap:8px;
          font-size:14px; font-weight:600;
          color:var(--color-text-primary,#e2e8f0);
        }
        .pap-count {
          background:var(--color-background-warning,#78350f);
          color:var(--color-text-warning,#fbbf24);
          font-size:11px; font-weight:700;
          padding:2px 7px; border-radius:99px;
        }
        .pap-close {
          background:none; border:none;
          color:var(--color-text-tertiary,#64748b);
          font-size:20px; cursor:pointer; line-height:1; padding:0 4px;
        }
        .pap-close:hover { color:var(--color-text-primary,#e2e8f0); }
        .pap-body { flex:1; overflow-y:auto; padding:16px; }
        .pap-empty {
          font-size:13px; color:var(--color-text-tertiary,#64748b);
          text-align:center; padding:28px 0;
        }
        .pap-card {
          background:var(--color-background-secondary,#16213e);
          border:1px solid var(--color-border-tertiary,#223);
          border-radius:8px; padding:14px; margin-bottom:10px;
        }
        .pap-card.pap-active { border-color:var(--color-border-accent,#6366f1); }
        .pap-card-header {
          display:flex; align-items:center; justify-content:space-between;
          margin-bottom:10px;
        }
        .pap-plugin-name {
          font-size:13px; font-weight:600;
          color:var(--color-text-primary,#e2e8f0);
          font-family:var(--font-mono,monospace);
        }
        .pap-risk { font-size:10px; font-weight:600; padding:2px 8px; border-radius:99px; text-transform:uppercase; }
        .pap-risk.low    { background:#14532d; color:#86efac; }
        .pap-risk.medium { background:#78350f; color:#fbbf24; }
        .pap-risk.high   { background:#7f1d1d; color:#fca5a5; }
        .pap-row {
          display:grid; grid-template-columns:90px 1fr;
          gap:4px 8px; font-size:12px; margin-bottom:5px;
        }
        .pap-row-label { color:var(--color-text-tertiary,#64748b); }
        .pap-row-value { color:var(--color-text-secondary,#94a3b8); word-break:break-all; }
        .pap-desc {
          font-size:12px; color:var(--color-text-secondary,#94a3b8);
          margin-top:8px; padding-top:8px;
          border-top:1px solid var(--color-border-tertiary,#223);
          line-height:1.5;
        }
        .pap-nav {
          display:flex; align-items:center; justify-content:center;
          gap:12px; padding:8px 0 0;
          font-size:12px; color:var(--color-text-tertiary,#64748b);
        }
        .pap-nav button {
          background:none;
          border:1px solid var(--color-border-tertiary,#334);
          border-radius:6px; color:var(--color-text-secondary,#94a3b8);
          padding:3px 10px; cursor:pointer; font-size:12px;
        }
        .pap-nav button:disabled { opacity:.35; cursor:default; }
        .pap-footer {
          display:flex; gap:10px; padding:12px 16px;
          border-top:1px solid var(--color-border-tertiary,#223);
          background:var(--color-background-secondary,#16213e);
        }
        .pap-btn {
          flex:1; padding:10px 0; border:none; border-radius:8px;
          font-size:13px; font-weight:600; cursor:pointer; transition:opacity .15s;
        }
        .pap-btn:hover { opacity:.85; }
        .pap-btn--approve { background:#166534; color:#bbf7d0; }
        .pap-btn--deny    { background:#7f1d1d; color:#fecaca; }
        .plugin-approval-badge {
          display:inline-flex; align-items:center; justify-content:center;
          min-width:18px; height:18px; padding:0 5px;
          background:#fbbf24; color:#1a1a2e;
          border-radius:99px; font-size:10px; font-weight:700;
          margin-left:6px; vertical-align:middle;
        }
      `;
      document.head.appendChild(style);
    }

    // ── Panel DOM builder ────────────────────────────────────────────────

    function _ensurePanel() {
      if (_getPanel()) return;
      _injectCSS();

      // Backdrop
      const bd = document.createElement("div");
      bd.id = "pluginApprovalBackdrop";
      bd.className = "pap-backdrop pap-hidden";
      bd.addEventListener("click", hide);
      document.body.appendChild(bd);

      // Panel
      const panel = document.createElement("div");
      panel.id = "pluginApprovalPanel";
      panel.className = "plugin-approval-panel pap-hidden";
      panel.setAttribute("role", "dialog");
      panel.setAttribute("aria-modal", "true");
      panel.setAttribute("aria-label", "Plugin Approval");
      panel.innerHTML = `
        <div class="pap-header">
          <div class="pap-title">
            <span>🔌</span>
            <span>Plugin Approval</span>
            <span class="pap-count" id="papCount">0</span>
          </div>
          <button class="pap-close" id="papCloseBtn" aria-label="Close">×</button>
        </div>
        <div class="pap-body" id="papBody">
          <div class="pap-empty">No plugins awaiting approval.</div>
        </div>
        <div class="pap-footer pap-hidden" id="papFooter">
          <button class="pap-btn pap-btn--deny"    id="papDenyBtn">✕ Reject</button>
          <button class="pap-btn pap-btn--approve" id="papApproveBtn">✓ Approve &amp; Deploy</button>
        </div>
      `;
      document.body.appendChild(panel);

      // Wire footer buttons
      document.getElementById("papApproveBtn").addEventListener("click", () => respond(true));
      document.getElementById("papDenyBtn").addEventListener("click",    () => respond(false));
      document.getElementById("papCloseBtn").addEventListener("click",   hide);

      // Keyboard: Escape = close
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && _panelVisible) hide();
      });
    }

    // ── Content renderer ─────────────────────────────────────────────────

    function _refresh() {
      _ensurePanel();
      const body   = document.getElementById("papBody");
      const footer = document.getElementById("papFooter");
      const count  = document.getElementById("papCount");
      if (!body) return;

      if (count) count.textContent = _queue.length;

      if (_queue.length === 0) {
        body.innerHTML = '<div class="pap-empty">No plugins awaiting approval.</div>';
        if (footer) footer.classList.add("pap-hidden");
        return;
      }

      _currentIdx = Math.min(_currentIdx, _queue.length - 1);
      const item = _queue[_currentIdx];
      const risk = (item.risk_level || item.risk || "medium").toLowerCase();
      const name = esc(item.name || item.plugin_name || "unknown");

      body.innerHTML = `
        <div class="pap-card pap-active">
          <div class="pap-card-header">
            <span class="pap-plugin-name">${name}</span>
            <span class="pap-risk ${risk}">${esc(risk)} risk</span>
          </div>
          <div class="pap-row">
            <span class="pap-row-label">Intent</span>
            <span class="pap-row-value">${esc(item.intent || "—")}</span>
          </div>
          <div class="pap-row">
            <span class="pap-row-label">Directory</span>
            <span class="pap-row-value">${esc(item.plugin_dir || "—")}</span>
          </div>
          <div class="pap-row">
            <span class="pap-row-label">Reason</span>
            <span class="pap-row-value">${esc(item.reason || "—")}</span>
          </div>
          ${item.description
            ? `<div class="pap-desc">${esc(item.description)}</div>`
            : ""}
        </div>
        ${_queue.length > 1 ? `
          <div class="pap-nav">
            <button id="papPrev" ${_currentIdx === 0 ? "disabled" : ""}>← Prev</button>
            <span>${_currentIdx + 1} / ${_queue.length}</span>
            <button id="papNext" ${_currentIdx >= _queue.length - 1 ? "disabled" : ""}>Next →</button>
          </div>` : ""}
      `;

      if (_queue.length > 1) {
        const prev = document.getElementById("papPrev");
        const next = document.getElementById("papNext");
        if (prev) prev.addEventListener("click", () => { _currentIdx = Math.max(0, _currentIdx - 1); _refresh(); });
        if (next) next.addEventListener("click", () => { _currentIdx = Math.min(_queue.length - 1, _currentIdx + 1); _refresh(); });
      }

      if (footer) footer.classList.remove("pap-hidden");
    }

    // ── Public API ───────────────────────────────────────────────────────

    function show(data) {
      _ensurePanel();
      // Deduplicate by plugin name
      const name = data.name || data.plugin_name;
      if (name && _queue.some(q => (q.name || q.plugin_name) === name)) return;

      _queue.push(data);
      _currentIdx = _queue.length - 1;
      _updateBadge();

      toast.show(
        "warn",
        "Plugin Approval Required",
        `"${esc(name || "new plugin")}" ready — ${_queue.length} pending`,
        8000,
      );

      _panelVisible = true;
      _getPanel().classList.remove("pap-hidden");
      _getBackdrop().classList.remove("pap-hidden");
      _refresh();
    }

    function hide() {
      const panel = _getPanel();
      const bd    = _getBackdrop();
      if (panel) panel.classList.add("pap-hidden");
      if (bd)    bd.classList.add("pap-hidden");
      _panelVisible = false;
    }

    function respond(approved) {
      if (_queue.length === 0) return;
      const item = _queue[_currentIdx];
      const name = item.name || item.plugin_name || "";

      if (approved) {
        ws.send({
          action:     "APPROVE_PLUGIN",
          name:       name,
          intent:     item.intent     || name,
          plugin_dir: item.plugin_dir || "",
        });
        toast.show("success", "Plugin Approved", `"${esc(name)}" sent for deployment.`);
      } else {
        const reason = window.prompt(`Reason for rejecting "${name}"?`) || "Rejected by user";
        ws.send({ action: "REJECT_PLUGIN", name, reason });
        toast.show("warn", "Plugin Rejected", `"${esc(name)}" rejected.`);
      }

      _queue.splice(_currentIdx, 1);
      _currentIdx = Math.max(0, _currentIdx - 1);
      _updateBadge();

      if (_queue.length === 0) { hide(); }
      else { _refresh(); }
    }

    function onLifecycleEvent(etype, data) {
      // Remove from queue if approved/rejected via another path (auto-approve, other tab)
      if (etype === "plugin_approved" || etype === "plugin_rejected") {
        const name = data?.name || data?.plugin_name;
        if (name) {
          const idx = _queue.findIndex(q => (q.name || q.plugin_name) === name);
          if (idx >= 0) {
            _queue.splice(idx, 1);
            _currentIdx = Math.max(0, _currentIdx - 1);
            _updateBadge();
            _refresh();
          }
        }
      }
      if (etype === "plugin_installed") {
        const n = data?.name || data?.plugin_name || "plugin";
        toast.show("success", "Plugin Installed", `"${esc(n)}" is now active.`);
      }
      if (etype === "plugin_generation_failed") {
        toast.show("error", "Plugin Generation Failed", esc(data?.reason || data?.name || ""));
      }
      if (etype === "plugin_validation_failed") {
        toast.show("warn", "Plugin Validation Failed",
          `"${esc(data?.name || "")}" failed at stage "${esc(data?.stage || "?")}".`);
      }
    }

    return { show, hide, respond, onLifecycleEvent };
  })();


  // ═══════════════════════════════════════════════════════════════════════
  // SAFETY CONFIRMATION
  // ═══════════════════════════════════════════════════════════════════════

  const safety = {
    /** Show the modal for a confirmation_required event. */
    show(data, callback) {
      const overlay = $("#confirmOverlay");
      if (!overlay) return;

      // Populate fields
      const risk = (data.risk || data.risk_level || "medium").toLowerCase();
      const badge = $("#confirmRiskBadge");
      if (badge) {
        badge.textContent = `${risk} risk`;
        badge.className   = `confirm-risk-badge${risk === "high" ? " high" : ""}`;
      }
      const title = $("#confirmTitle");
      if (title) title.textContent = data.title || "Safety Confirmation Required";
      const sub = $(".confirm-subtitle");
      if (sub) sub.textContent = data.subtitle || "Review before the agent proceeds";

      const actionEl  = $("#confirmAction");
      const sourceEl  = $("#confirmSource");
      const taskIdEl  = $("#confirmTaskId");
      const commandEl = $("#confirmCommand");
      if (actionEl)  actionEl.textContent  = data.action   || "—";
      if (sourceEl)  sourceEl.textContent  = data.source   || "—";
      if (taskIdEl)  taskIdEl.textContent  = data.task_id  || "—";
      if (commandEl) commandEl.textContent = data.command  || JSON.stringify(data.payload || {}, null, 2);

      // Callback (used by system.requestShutdown and similar internal callers
      // that don't go through the EventBus confirmation flow)
      _confirmCallback = callback || null;

      // Start timer
      clearTimeout(_confirmTimer);
      const fill  = $("#confirmTimerFill");
      const label = $("#confirmTimerLabel");
      if (fill) {
        fill.style.transition = "none";
        fill.style.width = "100%";
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            fill.style.transition = `width ${CONFIG.CONFIRM_TIMEOUT_MS}ms linear`;
            fill.style.width = "0%";
          });
        });
      }

      let remaining = Math.floor(CONFIG.CONFIRM_TIMEOUT_MS / 1000);
      const tick = setInterval(() => {
        remaining--;
        if (label) label.textContent = `${remaining}s to auto-deny`;
        if (remaining <= 0) clearInterval(tick);
      }, 1000);

      _confirmTimer = setTimeout(() => {
        clearInterval(tick);
        safety.respond(false);
      }, CONFIG.CONFIRM_TIMEOUT_MS);

      overlay.style.display = "flex";
      // Trap focus in modal
      const firstBtn = $("#confirmDenyBtn");
      if (firstBtn) firstBtn.focus();
    },

    /**
     * Called when the user clicks Allow or Deny (or presses Escape).
     *
     * If a _confirmCallback is set (internal callers like requestShutdown),
     * invoke it directly — no WebSocket message needed.
     *
     * Otherwise this is an agent confirmation_required flow:
     *   Send {"action": "dashboard_command", "type": "confirm_approved"|"confirm_denied",
     *          "task_id": "<id>"}
     *   websocket.py receives it, matches the DASHBOARD_COMMAND branch, and
     *   publishes user_response_received on the EventBus, which
     *   ConfirmationManager.handle_user_response() then processes.
     */
    respond(approved) {
      clearTimeout(_confirmTimer);
      const overlay = $("#confirmOverlay");
      if (overlay) overlay.style.display = "none";

      if (_confirmCallback) {
        _confirmCallback(approved);
        _confirmCallback = null;
        return;
      }

      // Agent confirmation flow — include task_id so the backend can
      // unambiguously match the response to the paused task.
      ws.send({
        action:  "dashboard_command",
        type:    approved ? "confirm_approved" : "confirm_denied",
        task_id: _pendingConfirmTaskId,   // may be null for legacy callers
      });

      // Clear local state — the confirmation is resolved from our side.
      _pendingConfirmTaskId = null;

      if (approved) {
        toast.show("success", "Confirmed", "Action approved — agent proceeding.");
      } else {
        toast.show("warn", "Denied", "Action denied — agent will skip this step.");
      }
    },
  };


  // ═══════════════════════════════════════════════════════════════════════
  // TOAST NOTIFICATIONS
  // ═══════════════════════════════════════════════════════════════════════

  const TOAST_ICONS = {
    success: "✓",
    warn:    "!",
    error:   "✕",
    info:    "i",
  };

  const toast = {
    show(type, title, message, duration = CONFIG.TOAST_DURATION_MS) {
      const stack = $("#toastStack");
      if (!stack) return;

      const cls   = type === "error" ? "danger" : type;
      const icon  = TOAST_ICONS[type] || "i";
      const id    = `toast-${Date.now()}-${Math.random().toString(36).slice(2,6)}`;

      const el = document.createElement("div");
      el.className = `toast toast--${cls}`;
      el.id = id;
      el.setAttribute("role", "alert");
      el.innerHTML = `
        <div class="toast-icon">${esc(icon)}</div>
        <div class="toast-content">
          <div class="toast-title">${esc(title)}</div>
          <div class="toast-message">${esc(message)}</div>
        </div>
        <button class="toast-close" onclick="document.getElementById('${id}')?.remove()" aria-label="Dismiss">×</button>
      `;

      stack.appendChild(el);

      // Auto-dismiss
      setTimeout(() => {
        if (el.parentNode) {
          el.style.transition = "opacity .3s, transform .3s";
          el.style.opacity = "0";
          el.style.transform = "translateX(12px)";
          setTimeout(() => el.remove(), 320);
        }
      }, duration);
    },
  };


  // ═══════════════════════════════════════════════════════════════════════
  // COMPONENT REGISTRY
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Component files call App.registerComponent(name, { init, onEvent }) to
   * hook into the application lifecycle.
   *
   * init(mountEl, config) — called on DOMContentLoaded with the mount element
   * onEvent(eventType, data) — called when a WS event matching eventType arrives
   */
  function registerComponent(name, { init, onEvent } = {}) {
    _components[name] = { init, onEvent };
    // Subscribe onEvent to WS if provided
    if (onEvent) {
      ws.on("*", (data, msg) => {
        try { onEvent(msg.event_type, data, msg); } catch (e) {
          console.warn(`[App] component '${name}' onEvent error:`, e);
        }
      });
    }
  }


  // ═══════════════════════════════════════════════════════════════════════
  // INIT
  // ═══════════════════════════════════════════════════════════════════════

  function _init() {
    // 1. Theme
    theme.init();

    // 2. Connect WS
    _connect();

    // 3. Health poll
    health.poll();
    _healthInterval = setInterval(() => {
      health.poll();
      health.pollDetailed();
    }, CONFIG.HEALTH_POLL_MS);

    // 4. Uptime ticker
    _uptimeInterval = setInterval(() => {
      _uptimeSecs++;
      const el = $("#topbarUptime");
      if (el) el.textContent = `uptime ${fmtUptime(_uptimeSecs)}`;
    }, CONFIG.UPTIME_TICK_MS);

    // 5. Fetch system info for env/version display
    apiFetch("/api/system/info").then(({ ok, data }) => {
      if (!ok || !data) return;
      const env = $("#topbarEnv");
      if (env)  env.textContent  = data.environment || "—";
      const ver = $(".logo-version");
      if (ver) {
        ver.textContent = `v${data.version || "?"}`;
        const agentVersion = $("#agentVersion");
        if (agentVersion) agentVersion.textContent = `v${data.version || "?"}`;
      }
    });

    // 6. Fetch initial input mode from the server (authoritative source).
    apiFetch("/api/system/input-mode").then(({ ok, data }) => {
      if (ok && data?.mode) {
        _currentMode = data.mode;
        inputMode._render(data.mode);
      }
    });

    // 7. Fetch plugin count for badge
    apiFetch("/api/plugins").then(({ ok, data }) => {
      if (ok && data) {
        const badge = $("#pluginCountBadge");
        if (badge) badge.textContent = data.count ?? data.plugins?.length ?? 0;
      }
    });

    // 8. Init registered components
    for (const [name, comp] of Object.entries(_components)) {
      if (!comp.init) continue;
      const mountId  = `${name}Mount`;
      const mountEl  = $(`#${mountId}`);
      if (mountEl) {
        try { comp.init(mountEl, CONFIG); }
        catch (e) { console.error(`[App] Failed to init component '${name}':`, e); }
      }
    }

    // 9. Mount the ModeSwitcher component into #modeSwitcherMount if present.
    //    ModeSwitcher connects to ws via the onEvent() API so it stays in sync
    //    with input_mode_changed events without polling.
    const switcherMount = $("#modeSwitcherMount");
    if (switcherMount) {
      import("../components/mode_switcher.js")
        .then(({ ModeSwitcher }) => {
          const switcher = new ModeSwitcher(switcherMount);
          switcher.connect(ws);
        })
        .catch(err => {
          console.warn("[App] ModeSwitcher failed to load:", err);
        });
    }

    // 10. Handle input_mode_changed from the EventBus (via WebSocket bridge).
    //     This fires whenever ModeManager completes a switch — including
    //     switches triggered from OTHER tabs or directly via the API.
    //     Keeps all open dashboard tabs in sync without polling.
    ws.on("input_mode_changed", (data) => {
      const newMode = data?.new_mode || data?.mode;
      if (newMode) {
        inputMode.applyFromServer(newMode);
        const label = newMode === "none"
          ? "All modes deactivated"
          : `${newMode.charAt(0).toUpperCase() + newMode.slice(1)} mode now active`;
        toast.show("info", "Input Mode Changed", label);
      }
    });

    // 11. WS event → error badge on logs nav item
    ws.on("error", () => {
      const badge = $("#errorCountBadge");
      if (!badge) return;
      const count = parseInt(badge.textContent || "0", 10);
      badge.textContent = count + 1;
      badge.style.display = "";
    });

    // 12. Handle system_shutting_down event
    ws.on("system_shutting_down", () => {
      _setWsState("disconnected");
      toast.show("warn", "System Shutdown", "Agent is shutting down. Dashboard will disconnect.");
    });

    // 13. Handle reflection_complete
    ws.on("reflection_complete", (data) => {
      toast.show("success", "Reflection Complete", "Reflector finished analysis. Check Evolution page.");
    });

    // 14. Handle capabilities_remapped
    ws.on("capabilities_remapped", () => {
      toast.show("success", "Capabilities Remapped", "Capability graph rebuilt.");
    });

    // 16. Plugin approval — also listen on plugin_ready_for_approval (secondary
    //     event name the generator publishes directly).
    ws.on("plugin_ready_for_approval", (data) => {
      pluginApproval.show(data);
    });

    // 17. Inject plugin approval badge next to Plugins nav item if not in HTML
    (function _ensureApprovalBadge() {
      if (document.getElementById("pluginApprovalBadge")) return;
      const pluginsNav = Array.from(document.querySelectorAll(".nav-item"))
        .find(el => el.textContent.trim().toLowerCase().includes("plugin"));
      if (pluginsNav) {
        const badge = document.createElement("span");
        badge.id = "pluginApprovalBadge";
        badge.className = "plugin-approval-badge";
        badge.style.display = "none";
        pluginsNav.appendChild(badge);
      }
    })();

    // 15. Keyboard shortcut: Escape closes confirm modal
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        const overlay = $("#confirmOverlay");
        if (overlay && overlay.style.display !== "none") {
          safety.respond(false);
        }
      }
    });

    console.info("[Operonix] Dashboard initialised.");
  }

  // Boot on DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _init);
  } else {
    _init();
  }


  // ═══════════════════════════════════════════════════════════════════════
  // PUBLIC API
  // ═══════════════════════════════════════════════════════════════════════

  return {
    // Core utils (exposed for component files)
    $,
    $$,
    apiFetch,
    esc,
    fmtTime,
    fmtUptime,
    actionChipClass,
    CONFIG,

    // Subsystems
    theme,
    ws,
    inputMode,
    health,
    system,
    safety,
    pluginApproval,
    toast,

    // Navigation
    navigate,

    // Component system
    registerComponent,
  };

})();

// Make available globally so component files and inline onclick handlers work
window.App = App;