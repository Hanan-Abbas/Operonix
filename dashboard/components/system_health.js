/**
 * system_health.js
 *
 * Renders into: #systemHealthMount  (overview page, full width)
 *
 * Data sources:
 *   GET /api/health          → component status + uptime
 *   GET /api/health/detailed → cpu, ram, audio, stt, llm details
 *   GET /api/system/status   → active/pending/completed/failed task counts
 *   WS  health_update        → real-time push from App.health.pollDetailed()
 *
 * No App.registerComponent() called at parse time — deferred to
 * DOMContentLoaded so App is guaranteed to exist.
 */

(function () {
  "use strict";

  // ── Rendering helpers ────────────────────────────────────────────────────

  function statusClass(val) {
    if (val === "running" || val === "loaded" || val === "ready" || val === "healthy") return "chip--success";
    if (val === "degraded") return "chip--warn";
    return "chip--danger";
  }

  function statusLabel(val) {
    const map = {
      running:    "Running",
      down:       "Down",
      loaded:     "Loaded",
      not_loaded: "Not Loaded",
      ready:      "Ready",
      not_ready:  "Not Ready",
      healthy:    "Healthy",
      degraded:   "Degraded",
      unhealthy:  "Unhealthy",
    };
    return map[val] || val || "—";
  }

  // ── Build the static HTML shell once ────────────────────────────────────

  function buildShell(mount) {
    mount.innerHTML = `
      <div class="card" id="healthCard">
        <div class="card-header">
          <span class="card-title">System Health</span>
          <span class="card-subtitle" id="healthPollAge">polling…</span>
          <div class="card-actions">
            <button class="card-btn" id="healthDetailBtn" onclick="SystemHealth.toggleDetail()">
              Details
            </button>
            <button class="card-btn" id="healthRefreshBtn" onclick="SystemHealth.refresh()">
              Refresh
            </button>
          </div>
        </div>

        <!-- Component pills row -->
        <div class="sh-pills-row" id="shPillsRow">
          ${buildPills()}
        </div>

        <!-- Task counters -->
        <div class="sh-counters" id="shCounters">
          ${buildCounters()}
        </div>

        <!-- Resource bars (cpu / ram) -->
        <div class="sh-resources" id="shResources" style="display:none">
          ${buildResources()}
        </div>

        <!-- Detailed panels (audio / stt / llm) -->
        <div class="sh-detail-panels" id="shDetailPanels" style="display:none">
          ${buildDetailPanels()}
        </div>
      </div>
    `;
  }

  function buildPills() {
    const defs = [
      { id: "pill-event_bus",    label: "Event Bus"    },
      { id: "pill-orchestrator", label: "Orchestrator" },
      { id: "pill-executor",     label: "Executor"     },
      { id: "pill-llm_client",   label: "LLM Client"   },
      { id: "pill-stt_model",    label: "STT Model"    },
    ];
    return defs.map(d => `
      <div class="sh-pill" id="${d.id}">
        <div class="sh-pill-dot"></div>
        <div class="sh-pill-body">
          <span class="sh-pill-name">${d.label}</span>
          <span class="sh-pill-status chip chip--muted">—</span>
        </div>
        <span class="sh-pill-meta"></span>
      </div>
    `).join("");
  }

  function buildCounters() {
    const defs = [
      { id: "cnt-uptime",    label: "Uptime",          unit: "" },
      { id: "cnt-active",    label: "Active Tasks",    unit: "" },
      { id: "cnt-completed", label: "Completed",       unit: "" },
      { id: "cnt-failed",    label: "Failed",          unit: "" },
      { id: "cnt-pending",   label: "Pending",         unit: "" },
      { id: "cnt-memory",    label: "Memory Entries",  unit: "" },
    ];
    return defs.map(d => `
      <div class="sh-counter">
        <span class="sh-counter-label">${d.label}</span>
        <span class="sh-counter-val" id="${d.id}">—</span>
      </div>
    `).join("");
  }

  function buildResources() {
    return `
      <div class="sh-res-item">
        <div class="sh-res-header">
          <span class="sh-res-label">CPU</span>
          <span class="sh-res-val" id="res-cpu">—</span>
        </div>
        <div class="res-bar"><div class="res-fill res-fill--accent" id="res-cpu-bar" style="width:0%"></div></div>
      </div>
      <div class="sh-res-item">
        <div class="sh-res-header">
          <span class="sh-res-label">RAM</span>
          <span class="sh-res-val" id="res-ram">—</span>
        </div>
        <div class="res-bar"><div class="res-fill res-fill--violet" id="res-ram-bar" style="width:0%"></div></div>
      </div>
    `;
  }

  function buildDetailPanels() {
    return `
      <div class="sh-detail-section">
        <div class="sh-detail-title">Audio</div>
        <div class="sh-detail-body" id="detailAudio">
          <span class="text-muted" style="font-size:11px">Loading…</span>
        </div>
      </div>
      <div class="sh-detail-section">
        <div class="sh-detail-title">STT Model</div>
        <div class="sh-detail-body" id="detailStt">
          <span class="text-muted" style="font-size:11px">Loading…</span>
        </div>
      </div>
      <div class="sh-detail-section">
        <div class="sh-detail-title">LLM Providers</div>
        <div class="sh-detail-body" id="detailLlm">
          <span class="text-muted" style="font-size:11px">Loading…</span>
        </div>
      </div>
    `;
  }

  // ── Scoped styles injected once ──────────────────────────────────────────

  function injectStyles() {
    if (document.getElementById("sh-styles")) return;
    const s = document.createElement("style");
    s.id = "sh-styles";
    s.textContent = `
      .sh-pills-row {
        display: grid;
        grid-template-columns: repeat(5, 1fr);
        gap: 0;
        border-bottom: 1px solid var(--border-subtle);
      }
      .sh-pill {
        display: flex;
        flex-direction: column;
        gap: 6px;
        padding: 13px 16px;
        border-right: 1px solid var(--border-subtle);
        transition: background var(--t-fast);
      }
      .sh-pill:last-child { border-right: none; }
      .sh-pill:hover { background: var(--bg-elevated); }
      .sh-pill-dot {
        width: 8px; height: 8px; border-radius: 50%;
        background: var(--border-strong);
        transition: background var(--t-base), box-shadow var(--t-base);
      }
      .sh-pill.ok  .sh-pill-dot { background: var(--clr-success); box-shadow: 0 0 6px var(--clr-success-dim); }
      .sh-pill.warn .sh-pill-dot { background: var(--clr-warn); }
      .sh-pill.down .sh-pill-dot { background: var(--clr-danger); }
      .sh-pill-body { display: flex; flex-direction: column; gap: 3px; }
      .sh-pill-name { font-size: 10px; font-weight: 700; letter-spacing: .8px; text-transform: uppercase; color: var(--text-muted); }
      .sh-pill-status { font-size: 10px; align-self: flex-start; }
      .sh-pill-meta { font-family: 'JetBrains Mono', monospace; font-size: 9.5px; color: var(--text-muted); min-height: 14px; }

      .sh-counters {
        display: grid;
        grid-template-columns: repeat(6, 1fr);
        border-bottom: 1px solid var(--border-subtle);
      }
      .sh-counter {
        display: flex; flex-direction: column; gap: 3px;
        padding: 11px 16px;
        border-right: 1px solid var(--border-subtle);
      }
      .sh-counter:last-child { border-right: none; }
      .sh-counter-label { font-size: 9.5px; font-weight: 600; letter-spacing: .8px; text-transform: uppercase; color: var(--text-muted); }
      .sh-counter-val { font-family: 'JetBrains Mono', monospace; font-size: 17px; font-weight: 500; color: var(--text-primary); }
      #cnt-failed { color: var(--clr-danger); }

      .sh-resources {
        display: grid; grid-template-columns: 1fr 1fr; gap: 0;
        padding: 12px 16px; gap: 12px;
        border-bottom: 1px solid var(--border-subtle);
      }
      .sh-res-item { display: flex; flex-direction: column; gap: 6px; }
      .sh-res-header { display: flex; justify-content: space-between; }
      .sh-res-label { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: .8px; color: var(--text-muted); }
      .sh-res-val { font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-secondary); }

      .sh-detail-panels {
        display: grid; grid-template-columns: repeat(3, 1fr); gap: 0;
      }
      .sh-detail-section {
        padding: 12px 16px;
        border-right: 1px solid var(--border-subtle);
      }
      .sh-detail-section:last-child { border-right: none; }
      .sh-detail-title { font-size: 10px; font-weight: 700; letter-spacing: .8px; text-transform: uppercase; color: var(--text-muted); margin-bottom: 8px; }
      .sh-detail-body { display: flex; flex-direction: column; gap: 4px; }
      .sh-kv { display: flex; justify-content: space-between; align-items: baseline; }
      .sh-kv-k { font-size: 11px; color: var(--text-secondary); }
      .sh-kv-v { font-family: 'JetBrains Mono', monospace; font-size: 10.5px; color: var(--text-primary); }
      .sh-provider-row { display: flex; justify-content: space-between; align-items: center; padding: 3px 0; }
      .sh-provider-name { font-size: 11px; color: var(--text-secondary); }

      @media (max-width: 1100px) {
        .sh-pills-row { grid-template-columns: repeat(3, 1fr); }
        .sh-counters  { grid-template-columns: repeat(3, 1fr); }
        .sh-detail-panels { grid-template-columns: 1fr; }
      }
    `;
    document.head.appendChild(s);
  }

  // ── State ────────────────────────────────────────────────────────────────

  let _detailOpen   = false;
  let _pollAgeTimer = null;
  let _lastPollMs   = 0;

  // ── Update functions ─────────────────────────────────────────────────────

  function applyHealth(data) {
    _lastPollMs = Date.now();

    // Pills
    const comps = data.components || {};

    function setPill(id, val, meta) {
      const pill = document.getElementById(id);
      if (!pill) return;
      const dot    = pill.querySelector(".sh-pill-dot");
      const status = pill.querySelector(".sh-pill-status");
      const metaEl = pill.querySelector(".sh-pill-meta");

      const ok = (val === "running" || val === "loaded" || val === "ready");
      const dn = (val === "down" || val === "not_loaded" || val === "not_ready");

      pill.className  = `sh-pill ${ok ? "ok" : dn ? "down" : "warn"}`;
      status.className = `sh-pill-status chip ${statusClass(val)}`;
      status.textContent = statusLabel(val);
      if (metaEl && meta !== undefined) metaEl.textContent = meta;
    }

    setPill("pill-event_bus",    comps.event_bus    || "—");
    setPill("pill-orchestrator", comps.orchestrator || "—");
    setPill("pill-executor",     comps.executor     || "—");

    const stt = comps.stt_model;
    const sttVal = typeof stt === "object" ? (stt.loaded ? "loaded" : "not_loaded") : (stt || "—");
    const sttMeta = typeof stt === "object" && stt.size ? stt.size : "";
    setPill("pill-stt_model", sttVal, sttMeta);

    const llm = comps.llm_client;
    const llmVal = typeof llm === "object" ? (llm.ready ? "ready" : "not_ready") : (llm || "—");
    setPill("pill-llm_client", llmVal);

    // Uptime
    if (data.uptime_seconds != null) {
      const el = document.getElementById("cnt-uptime");
      if (el) {
        const s = Math.floor(data.uptime_seconds);
        const h = String(Math.floor(s / 3600)).padStart(2, "0");
        const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
        const sec = String(s % 60).padStart(2, "0");
        el.textContent = `${h}:${m}:${sec}`;
      }
    }
  }

  function applyStatus(data) {
    const set = (id, val, danger) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = val ?? "—";
      if (danger && parseInt(val, 10) > 0) el.style.color = "var(--clr-danger)";
      else el.style.color = "";
    };
    set("cnt-active",    data.active_tasks);
    set("cnt-pending",   data.pending_tasks);
    set("cnt-completed", data.completed_tasks);
    set("cnt-failed",    data.failed_tasks, true);
  }

  function applyDetailed(data) {
    // Resources
    const sys = data.system;
    if (sys) {
      const res = document.getElementById("shResources");
      if (res) res.style.display = _detailOpen ? "" : "none";

      const cpuEl  = document.getElementById("res-cpu");
      const cpuBar = document.getElementById("res-cpu-bar");
      const ramEl  = document.getElementById("res-ram");
      const ramBar = document.getElementById("res-ram-bar");

      if (cpuEl)  cpuEl.textContent  = `${Math.round(sys.cpu_percent)}%`;
      if (cpuBar) cpuBar.style.width = `${Math.min(100, Math.round(sys.cpu_percent))}%`;
      if (cpuBar) cpuBar.className = `res-fill ${sys.cpu_percent > 80 ? "res-fill--danger" : "res-fill--accent"}`;

      const ramUsed  = sys.memory_gb?.used  ?? 0;
      const ramTotal = sys.memory_gb?.total ?? 1;
      const ramPct   = Math.round((ramUsed / ramTotal) * 100);
      if (ramEl)  ramEl.textContent  = `${ramUsed.toFixed(1)} / ${ramTotal.toFixed(0)} GB (${ramPct}%)`;
      if (ramBar) ramBar.style.width = `${ramPct}%`;
      if (ramBar) ramBar.className = `res-fill ${ramPct > 85 ? "res-fill--danger" : ramPct > 70 ? "res-fill--warn" : "res-fill--violet"}`;
    }

    if (!_detailOpen) return;

    // Audio
    const audioEl = document.getElementById("detailAudio");
    if (audioEl) {
      const a = data.audio || {};
      if (!a.device) {
        audioEl.innerHTML = `<span class="chip chip--muted">Not available</span>`;
      } else {
        const dev = a.device;
        audioEl.innerHTML = `
          <div class="sh-kv"><span class="sh-kv-k">Device</span><span class="sh-kv-v">${App.esc(dev.name || "—")}</span></div>
          <div class="sh-kv"><span class="sh-kv-k">Sample Rate</span><span class="sh-kv-v">${dev.sample_rate || "—"} Hz</span></div>
          <div class="sh-kv"><span class="sh-kv-k">Channels</span><span class="sh-kv-v">${dev.channels || "—"}</span></div>
          <div class="sh-kv"><span class="sh-kv-k">Running</span>
            <span class="chip ${data.is_running ? "chip--success" : "chip--muted"}">${data.is_running ? "Yes" : "No"}</span>
          </div>
          <div class="sh-kv"><span class="sh-kv-k">Overflow</span><span class="sh-kv-v">${data.overflow_count ?? "—"}</span></div>
        `;
      }
    }

    // STT
    const sttEl = document.getElementById("detailStt");
    if (sttEl) {
      const comps = data.components || {};
      const stt = (typeof comps.stt_model === "object") ? comps.stt_model : {};
      if (!stt.loaded) {
        sttEl.innerHTML = `<span class="chip chip--danger">Not Loaded</span>`;
      } else {
        const m = stt;
        sttEl.innerHTML = `
          <div class="sh-kv"><span class="sh-kv-k">Size</span><span class="sh-kv-v">${App.esc(m.size || "—")}</span></div>
          <div class="sh-kv"><span class="sh-kv-k">Device</span><span class="sh-kv-v">${App.esc(m.device || "—")}</span></div>
          <div class="sh-kv"><span class="sh-kv-k">Compute</span><span class="sh-kv-v">${App.esc(m.compute_type || "—")}</span></div>
          <div class="sh-kv"><span class="sh-kv-k">Lang</span><span class="sh-kv-v">${App.esc(m.language || "en")}</span></div>
        `;
      }
    }

    // LLM
    const llmEl = document.getElementById("detailLlm");
    if (llmEl && data.components?.llm_client?.providers) {
      const providers = data.components.llm_client.providers;
      const primary   = data.components.llm_client.primary || "";
      llmEl.innerHTML = Object.entries(providers).map(([name, info]) => `
        <div class="sh-provider-row">
          <span class="sh-provider-name">${App.esc(name)}${name === primary ? " <span style='color:var(--clr-accent);font-size:9px'>primary</span>" : ""}</span>
          <span class="chip ${info.configured ? "chip--success" : "chip--muted"}">${info.configured ? "configured" : "not set"}</span>
        </div>
      `).join("");
    }
  }

  // ── Public API ───────────────────────────────────────────────────────────

  window.SystemHealth = {
    toggleDetail() {
      _detailOpen = !_detailOpen;
      const btn       = document.getElementById("healthDetailBtn");
      const resources = document.getElementById("shResources");
      const panels    = document.getElementById("shDetailPanels");
      if (resources) resources.style.display = _detailOpen ? "" : "none";
      if (panels)    panels.style.display    = _detailOpen ? "" : "none";
      if (btn)       btn.textContent         = _detailOpen ? "Hide Details" : "Details";
      if (_detailOpen) SystemHealth.refresh();
    },

    async refresh() {
      const btn = document.getElementById("healthRefreshBtn");
      if (btn) btn.textContent = "…";

      const [h, s, d] = await Promise.all([
        App.apiFetch("/api/health"),
        App.apiFetch("/api/system/status"),
        App.apiFetch("/api/health/detailed"),
      ]);

      if (h.ok && h.data) applyHealth(h.data);
      if (s.ok && s.data) applyStatus(s.data);
      if (d.ok && d.data) applyDetailed(d.data);

      if (btn) btn.textContent = "Refresh";
    },
  };

  // ── Component registration — deferred until App exists ──────────────────

  document.addEventListener("DOMContentLoaded", function () {
    injectStyles();

    const mount = document.getElementById("systemHealthMount");
    if (!mount) return;

    buildShell(mount);
    SystemHealth.refresh();

    // Poll age ticker
    _pollAgeTimer = setInterval(() => {
      const el = document.getElementById("healthPollAge");
      if (!el || !_lastPollMs) return;
      const age = Math.round((Date.now() - _lastPollMs) / 1000);
      el.textContent = `polled ${age}s ago`;
    }, 1000);

    // Subscribe to WS health updates (fired by App.health.pollDetailed)
    App.ws.on("health_update", (data) => {
      applyDetailed(data);
    });

    // Also update when task events arrive
    App.ws.on("action_taken",  () => SystemHealth.refresh());
    App.ws.on("task_complete", () => SystemHealth.refresh());
    App.ws.on("task_failed",   () => SystemHealth.refresh());
  });

}());