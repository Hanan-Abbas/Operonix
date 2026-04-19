/**
 * live_logs.js
 *
 * Renders into: #liveLogsMount      (overview, compact)
 *               #liveLogsFullMount  (full Logs page)
 *
 * Data sources:
 *   GET /api/logs/recent   → initial tail (ring buffer or file)
 *   GET /api/logs/levels   → level count summary
 *   GET /api/logs/files    → available log files
 *   GET /api/logs/stream   → SSE real-time tail
 *   DELETE /api/logs/clear → clear ring buffer or file
 *
 * Uses SSE (EventSource) for streaming, NOT WebSocket — matches /api/logs/stream.
 */

(function () {
  "use strict";

  // ── State ────────────────────────────────────────────────────────────────

  const MAX_ENTRIES   = 500;     // max rows kept in DOM/memory
  const COMPACT_LIMIT = 80;      // max rows shown in compact mode

  let _entries    = [];           // newest first in memory
  let _filter     = { level: "all", source: "all" };
  let _paused     = false;
  let _sseConn    = null;         // EventSource instance
  let _autoScroll = true;
  let _levelCounts = {};

  // ── Level config ─────────────────────────────────────────────────────────

  const LEVEL_META = {
    DEBUG:    { cls: "ll-lvl-debug",  label: "DEBUG",    chipCls: "chip--muted"   },
    INFO:     { cls: "ll-lvl-info",   label: "INFO",     chipCls: "chip--info"    },
    WARNING:  { cls: "ll-lvl-warn",   label: "WARN",     chipCls: "chip--warn"    },
    WARN:     { cls: "ll-lvl-warn",   label: "WARN",     chipCls: "chip--warn"    },
    ERROR:    { cls: "ll-lvl-error",  label: "ERROR",    chipCls: "chip--danger"  },
    CRITICAL: { cls: "ll-lvl-crit",   label: "CRIT",     chipCls: "chip--danger"  },
  };

  function levelMeta(level) {
    return LEVEL_META[(level || "").toUpperCase()] || LEVEL_META.INFO;
  }

  // ── Scoped styles ────────────────────────────────────────────────────────

  function injectStyles() {
    if (document.getElementById("ll-styles")) return;
    const s = document.createElement("style");
    s.id = "ll-styles";
    s.textContent = `
      .ll-stream {
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
        display: flex; flex-direction: column;
        overflow-y: auto;
        scroll-behavior: smooth;
      }
      .ll-row {
        display: grid;
        grid-template-columns: 68px 46px 90px 1fr;
        gap: 8px;
        align-items: baseline;
        padding: 3px 16px;
        border-radius: 2px;
        transition: background var(--t-fast);
        line-height: 1.5;
      }
      .ll-row:hover { background: var(--bg-elevated); }
      .ll-row.ll-is-error { background: var(--clr-danger-dim); }
      .ll-row.ll-is-crit  { background: var(--clr-danger-dim); border-left: 2px solid var(--clr-danger); }
      .ll-ts   { color: var(--text-muted); font-size: 10px; white-space: nowrap; }
      .ll-lvl  { font-size: 10px; font-weight: 700; }
      .ll-lvl-debug { color: var(--text-muted); }
      .ll-lvl-info  { color: var(--clr-info); }
      .ll-lvl-warn  { color: var(--clr-warn); }
      .ll-lvl-error { color: var(--clr-danger); }
      .ll-lvl-crit  { color: var(--clr-danger); }
      .ll-src  { color: var(--text-muted); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .ll-msg  { color: var(--text-secondary); word-break: break-word; }
      .ll-is-error .ll-msg, .ll-is-crit .ll-msg { color: var(--clr-danger); }

      .ll-level-summary {
        display: flex; gap: 0;
        border-bottom: 1px solid var(--border-subtle);
        padding: 6px 16px; flex-wrap: wrap; gap: 6px;
      }
      .ll-level-count {
        display: flex; align-items: center; gap: 5px;
        font-size: 11px; color: var(--text-secondary);
      }
      .ll-level-count-val { font-family: 'JetBrains Mono', monospace; font-weight: 600; }
      .ll-count-debug { color: var(--text-muted); }
      .ll-count-info  { color: var(--clr-info); }
      .ll-count-warn  { color: var(--clr-warn); }
      .ll-count-error { color: var(--clr-danger); }

      .ll-pause-banner {
        display: none; padding: 5px 16px;
        background: var(--clr-warn-dim); border-bottom: 1px solid var(--clr-warn);
        font-size: 11px; color: var(--clr-warn);
        font-family: 'JetBrains Mono', monospace;
      }
      .ll-pause-banner.visible { display: block; }
    `;
    document.head.appendChild(s);
  }

  // ── Row renderer ─────────────────────────────────────────────────────────

  function renderRow(entry) {
    const level = (entry.level || "INFO").toUpperCase();
    const meta  = levelMeta(level);
    const ts    = App.fmtTime(entry.timestamp);
    const src   = App.esc((entry.source || entry.name || "—").slice(0, 20));
    const msg   = App.esc(entry.message || entry.msg || "");

    const isError = level === "ERROR" || level === "CRITICAL";

    return `<div class="ll-row${isError ? ` ll-is-${level === "CRITICAL" ? "crit" : "error"}` : ""}">
      <span class="ll-ts">${ts}</span>
      <span class="ll-lvl ${meta.cls}">${meta.label}</span>
      <span class="ll-src">${src}</span>
      <span class="ll-msg">${msg}</span>
    </div>`;
  }

  // ── Filter ───────────────────────────────────────────────────────────────

  function matchesFilter(entry) {
    const level = (entry.level || "").toUpperCase();
    const src   = entry.source || entry.name || "";

    const levelOk = _filter.level === "all" ||
      (_filter.level === "debug"    && level === "DEBUG") ||
      (_filter.level === "info"     && level === "INFO") ||
      (_filter.level === "warning"  && (level === "WARNING" || level === "WARN")) ||
      (_filter.level === "error"    && (level === "ERROR" || level === "CRITICAL"));

    const srcOk = _filter.source === "all" || src.toLowerCase().includes(_filter.source.toLowerCase());
    return levelOk && srcOk;
  }

  // ── Stream management ────────────────────────────────────────────────────

  function startSse() {
    stopSse();
    const params = new URLSearchParams({ poll_ms: 600 });
    if (_filter.level !== "all")  params.append("level",  _filter.level.toUpperCase());
    if (_filter.source !== "all") params.append("source", _filter.source);

    try {
      _sseConn = new EventSource(`${App.CONFIG.API_BASE}/api/logs/stream?${params}`);
      _sseConn.onmessage = (evt) => {
        if (_paused) return;
        let entry;
        try { entry = JSON.parse(evt.data); } catch { return; }
        addEntry(entry);
      };
      _sseConn.onerror = () => {
        // SSE auto-reconnects — do nothing
      };
    } catch (e) {
      // SSE not available, fall back to WS events
    }
  }

  function stopSse() {
    if (_sseConn) {
      _sseConn.close();
      _sseConn = null;
    }
  }

  function addEntry(entry) {
    _entries.unshift(entry);                     // prepend (newest first)
    if (_entries.length > MAX_ENTRIES) _entries.pop();

    // Count for level badge
    const lvl = (entry.level || "INFO").toUpperCase();
    _levelCounts[lvl] = (_levelCounts[lvl] || 0) + 1;

    if (lvl === "ERROR" || lvl === "CRITICAL") {
      // Notify nav badge via WS error event listener (already in app.js)
    }

    // Efficiently prepend to stream divs rather than full re-render
    const streamEls = document.querySelectorAll(".ll-stream");
    streamEls.forEach(el => {
      if (!matchesFilter(entry)) return;
      const row = document.createElement("div");
      row.innerHTML = renderRow(entry);
      const firstChild = row.firstElementChild;
      if (firstChild) {
        el.insertBefore(firstChild, el.firstChild);
        // Prune excess DOM rows
        while (el.children.length > MAX_ENTRIES) el.removeChild(el.lastChild);
      }
      if (_autoScroll) el.scrollTop = 0;
    });

    updateLevelCounts();
  }

  function updateLevelCounts() {
    const items = document.querySelectorAll("[data-ll-count]");
    items.forEach(el => {
      const lvl = el.getAttribute("data-ll-count").toUpperCase();
      el.textContent = _levelCounts[lvl] || 0;
    });
  }

  // ── Build shell ──────────────────────────────────────────────────────────

  function buildShell(mount, compact) {
    const maxH = compact ? "220px" : "calc(100vh - 230px)";
    mount.innerHTML = `
      <div class="card">
        <div class="card-header">
          <span class="card-title">Live Logs</span>
          <span class="card-subtitle">SSE stream</span>
          <div class="card-actions">
            ${!compact ? `
              <select class="filter-select" id="ll-filter-level" onchange="LiveLogs.applyFilter()">
                <option value="all">All levels</option>
                <option value="debug">DEBUG</option>
                <option value="info">INFO</option>
                <option value="warning">WARNING</option>
                <option value="error">ERROR</option>
              </select>
              <select class="filter-select" id="ll-filter-source" onchange="LiveLogs.applyFilter()">
                <option value="all">All sources</option>
                <option value="brain">brain</option>
                <option value="executor">executor</option>
                <option value="memory">memory</option>
                <option value="safety">safety</option>
                <option value="planner">planner</option>
                <option value="orchestrator">orchestrator</option>
                <option value="learner">learner</option>
              </select>
            ` : ""}
            <button class="card-btn" id="ll-pause-btn" onclick="LiveLogs.togglePause()">Pause</button>
            <button class="card-btn card-btn--danger" onclick="LiveLogs.clearLogs()">Clear</button>
          </div>
        </div>

        <!-- Level counts -->
        <div class="ll-level-summary">
          <div class="ll-level-count"><span class="ll-count-info">INFO</span><span class="ll-level-count-val ll-count-info" data-ll-count="INFO">0</span></div>
          <div class="ll-level-count"><span class="ll-count-warn" style="margin-left:10px">WARN</span><span class="ll-level-count-val ll-count-warn" data-ll-count="WARNING">0</span></div>
          <div class="ll-level-count"><span class="ll-count-error" style="margin-left:10px">ERROR</span><span class="ll-level-count-val ll-count-error" data-ll-count="ERROR">0</span></div>
          <div class="ll-level-count"><span class="ll-count-debug" style="margin-left:10px">DEBUG</span><span class="ll-level-count-val ll-count-debug" data-ll-count="DEBUG">0</span></div>
        </div>

        <!-- Pause banner -->
        <div class="ll-pause-banner" id="ll-pause-banner">⏸ Stream paused — new logs are still received</div>

        <!-- Stream -->
        <div class="ll-stream" style="max-height:${maxH}; padding: 6px 0;"
             onmouseenter="LiveLogs.onStreamHover(true)"
             onmouseleave="LiveLogs.onStreamHover(false)">
          <div class="empty-state" style="padding:24px 0">
            <div class="empty-state-icon">⏳</div>
            <div class="empty-state-text">Connecting to log stream…</div>
          </div>
        </div>
      </div>
    `;
  }

  // ── Public API ───────────────────────────────────────────────────────────

  window.LiveLogs = {
    togglePause() {
      _paused = !_paused;
      const btn    = document.getElementById("ll-pause-btn");
      const banner = document.getElementById("ll-pause-banner");
      if (btn) btn.textContent = _paused ? "Resume" : "Pause";
      if (banner) banner.classList.toggle("visible", _paused);
    },

    applyFilter() {
      const lvl = document.getElementById("ll-filter-level");
      const src = document.getElementById("ll-filter-source");
      _filter.level  = lvl ? lvl.value : "all";
      _filter.source = src ? src.value : "all";
      // Re-render from memory
      LiveLogs._rerender();
      // Restart SSE with new filter params
      startSse();
    },

    _rerender() {
      const streamEls = document.querySelectorAll(".ll-stream");
      streamEls.forEach(el => {
        const filtered = _entries.filter(matchesFilter);
        const limit    = el.closest("#liveLogsMount") ? COMPACT_LIMIT : MAX_ENTRIES;
        el.innerHTML   = filtered.slice(0, limit).map(renderRow).join("");
      });
    },

    onStreamHover(entering) {
      // Pause auto-scroll when hovered so user can read
      _autoScroll = !entering;
    },

    async clearLogs() {
      if (!confirm("Clear log ring buffer?")) return;
      const { ok, error } = await App.apiFetch("/api/logs/clear", { method: "DELETE" });
      if (ok) {
        _entries = [];
        _levelCounts = {};
        const streamEls = document.querySelectorAll(".ll-stream");
        streamEls.forEach(el => {
          el.innerHTML = `<div class="empty-state" style="padding:24px 0"><div class="empty-state-icon">○</div><div class="empty-state-text">Log buffer cleared.</div></div>`;
        });
        updateLevelCounts();
        App.toast.show("success", "Logs Cleared", "Ring buffer cleared.");
      } else {
        App.toast.show("error", "Clear Failed", error);
      }
    },
  };

  // ── Component init ───────────────────────────────────────────────────────

  document.addEventListener("DOMContentLoaded", function () {
    injectStyles();

    const compact = document.getElementById("liveLogsMount");
    const full    = document.getElementById("liveLogsFullMount");

    if (compact) buildShell(compact, true);
    if (full)    buildShell(full,    false);

    // Load recent logs from ring buffer / file
    App.apiFetch("/api/logs/recent?limit=100").then(({ ok, data }) => {
      if (!ok || !data?.entries?.length) return;
      // entries are newest-last from the API, reverse to get newest-first
      const entries = [...data.entries].reverse();
      entries.forEach(e => _entries.push(e));
      LiveLogs._rerender();
    });

    // Load level counts
    App.apiFetch("/api/logs/levels").then(({ ok, data }) => {
      if (!ok || !data?.counts) return;
      _levelCounts = {};
      Object.entries(data.counts).forEach(([k, v]) => {
        _levelCounts[k.toUpperCase()] = v;
      });
      updateLevelCounts();
    });

    // Start SSE stream
    startSse();

    // Also listen for log events from WS EventBus as a fallback / supplement
    App.ws.on("log_entry", (data) => {
      if (!_paused && data) addEntry(data);
    });

    App.ws.on("error_occurred", (data) => {
      if (data) addEntry({ level: "ERROR", message: data.message || JSON.stringify(data), source: data.component || "unknown", timestamp: new Date().toISOString() });
    });
  });

}());