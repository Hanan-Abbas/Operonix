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
        safety.show(msg.data);
        return;
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

    async activate(mode) {
      if (inputMode._pending) return;
      if (mode === _currentMode) return;

      inputMode._pending = true;
      const prev = _currentMode;
      _currentMode = mode;
      inputMode._render(mode);

      const { ok, error } = await apiFetch("/api/system/input-mode", {
        method: "POST",
        body: JSON.stringify({ mode }),
      });

      if (!ok) {
        // Rollback
        _currentMode = prev;
        inputMode._render(prev);
        toast.show("error", "Mode Switch Failed", error || "Could not change input mode.");
      } else {
        const label = mode === "none" ? "All modes deactivated"
          : `${mode.charAt(0).toUpperCase() + mode.slice(1)} mode activated`;
        toast.show("success", "Input Mode", label);
      }
      inputMode._pending = false;
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
      voiceCard.classList.remove("active");
      panelCard.classList.remove("active");
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

      // Callback
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

    respond(approved) {
      clearTimeout(_confirmTimer);
      const overlay = $("#confirmOverlay");
      if (overlay) overlay.style.display = "none";

      if (_confirmCallback) {
        _confirmCallback(approved);
        _confirmCallback = null;
      } else {
        // Respond to the agent via WebSocket
        ws.send({
          action: "dashboard_command",
          type:   approved ? "confirm_approved" : "confirm_denied",
        });
      }

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

    // 6. Fetch initial input mode
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

    // 9. WS event → error badge on logs nav item
    ws.on("error", () => {
      const badge = $("#errorCountBadge");
      if (!badge) return;
      const count = parseInt(badge.textContent || "0", 10);
      badge.textContent = count + 1;
      badge.style.display = "";
    });

    // 10. Handle system_shutting_down event
    ws.on("system_shutting_down", () => {
      _setWsState("disconnected");
      toast.show("warn", "System Shutdown", "Agent is shutting down. Dashboard will disconnect.");
    });

    // 11. Handle reflection_complete
    ws.on("reflection_complete", (data) => {
      toast.show("success", "Reflection Complete", "Reflector finished analysis. Check Evolution page.");
    });

    // 12. Handle capabilities_remapped
    ws.on("capabilities_remapped", () => {
      toast.show("success", "Capabilities Remapped", "Capability graph rebuilt.");
    });

    // 13. Keyboard shortcut: Escape closes confirm modal
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
    toast,

    // Navigation
    navigate,

    // Component system
    registerComponent,
  };

})();

// Make available globally so component files and inline onclick handlers work
window.App = App;