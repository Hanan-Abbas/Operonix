/**
 * action_stream.js
 *
 * Renders into: #actionStreamMount  (overview, compact)
 *               #actionStreamFullMount  (full Actions page)
 *
 * Data sources:
 *   GET /api/actions/history  → initial load + filter results
 *   GET /api/actions/summary  → status counts for summary bar
 *   WS  action_started / action_complete / action_failed → live prepend
 *   DELETE /api/actions/clear → clear log
 */

(function () {
  "use strict";

  // ── State ────────────────────────────────────────────────────────────────

  const MAX_LIVE = 200;   // max items kept in memory
  let _items        = []; // { id, type, tool, target, status, ts, duration, error, params }
  let _filter       = { status: "all", type: "all" };
  let _expanded     = new Set();
  let _compactMode  = true;   // true on overview mount, false on full page
  let _summaryData  = { total: 0, by_status: {}, by_source: {} };

  // ── Template helpers ─────────────────────────────────────────────────────

  function chipForType(tool) {
    return `chip ${App.actionChipClass(tool || "")}`;
  }

  function chipForStatus(status) {
    if (!status) return "chip chip--muted";
    const s = status.toLowerCase();
    if (s === "success" || s === "done" || s === "complete") return "chip chip--success";
    if (s === "failed"  || s === "error")                    return "chip chip--danger";
    if (s === "running" || s === "active")                   return "chip chip--info";
    if (s === "pending" || s === "queued")                   return "chip chip--muted";
    return "chip chip--muted";
  }

  function statusDot(status) {
    const s = (status || "").toLowerCase();
    if (s === "running" || s === "active") return `<span class="running-dot"></span>`;
    return "";
  }

  function statusLabel(status) {
    const s = (status || "").toLowerCase();
    if (s === "success" || s === "done") return "done";
    if (s === "failed"  || s === "error") return "failed";
    if (s === "running" || s === "active") return "running";
    if (s === "pending" || s === "queued") return "queued";
    return status || "—";
  }

  function renderItem(item, compact) {
    const typeChip   = chipForType(item.tool || item.type || item.source);
    const statusChip = chipForStatus(item.status);
    const typeName   = (item.tool || item.type || item.source || "action").toLowerCase();
    const target     = item.target || item.description || item.message || "";
    const ts         = App.fmtTime(item.timestamp || item.ts);
    const expanded   = _expanded.has(item.task_id || item.id);

    const params = item.params || item.payload || item.metadata || {};
    const hasParams = Object.keys(params).length > 0;
    const hasError  = !!item.error;

    return `
      <div class="as-item ${expanded ? "as-item--expanded" : ""}" data-id="${App.esc(item.task_id || item.id || "")}">
        <div class="as-item-main" onclick="ActionStream.toggleExpand('${App.esc(item.task_id || item.id || "")}')">
          <span class="${typeChip}" style="flex-shrink:0">${App.esc(typeName)}</span>
          <div class="as-item-text">
            <span class="as-item-tool">${App.esc(typeName)}</span>
            ${target ? `<span class="as-item-target">${App.esc(String(target).slice(0, 80))}${String(target).length > 80 ? "…" : ""}</span>` : ""}
          </div>
          <div class="as-item-right">
            ${item.duration_ms ? `<span class="as-item-dur">${item.duration_ms}ms</span>` : ""}
            <span class="${statusChip}">${statusDot(item.status)}${statusLabel(item.status)}</span>
            <span class="as-item-ts">${ts}</span>
            ${(hasParams || hasError) ? `<span class="as-expand-icon">${expanded ? "▲" : "▼"}</span>` : ""}
          </div>
        </div>
        ${expanded && (hasParams || hasError) ? `
          <div class="as-item-detail">
            ${hasError ? `<div class="as-error-row"><span class="as-detail-label">Error</span><span class="as-error-msg">${App.esc(item.error)}</span></div>` : ""}
            ${hasParams ? `<div class="as-params-row"><span class="as-detail-label">Params</span><pre class="as-params-pre">${App.esc(JSON.stringify(params, null, 2))}</pre></div>` : ""}
          </div>
        ` : ""}
      </div>
    `;
  }

  // ── Scoped styles ────────────────────────────────────────────────────────

  function injectStyles() {
    if (document.getElementById("as-styles")) return;
    const s = document.createElement("style");
    s.id = "as-styles";
    s.textContent = `
      .as-list { display: flex; flex-direction: column; gap: 0; }
      .as-item {
        border-bottom: 1px solid var(--border-subtle);
        transition: background var(--t-fast);
      }
      .as-item:last-child { border-bottom: none; }
      .as-item-main {
        display: flex; align-items: center; gap: 9px;
        padding: 9px 16px; cursor: pointer;
      }
      .as-item:hover .as-item-main { background: var(--bg-elevated); }
      .as-item--expanded .as-item-main { background: var(--bg-elevated); }
      .as-item-text { flex: 1; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
      .as-item-tool { font-size: 12px; font-weight: 500; color: var(--text-primary); font-family: 'JetBrains Mono', monospace; }
      .as-item-target { font-size: 11px; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .as-item-right { display: flex; align-items: center; gap: 7px; flex-shrink: 0; }
      .as-item-dur { font-family: 'JetBrains Mono', monospace; font-size: 10px; color: var(--text-muted); }
      .as-item-ts  { font-family: 'JetBrains Mono', monospace; font-size: 10px; color: var(--text-muted); min-width: 60px; text-align: right; }
      .as-expand-icon { font-size: 9px; color: var(--text-muted); }
      .as-item-detail { padding: 0 16px 10px 16px; border-top: 1px solid var(--border-subtle); }
      .as-error-row, .as-params-row { display: flex; gap: 10px; margin-top: 8px; align-items: flex-start; }
      .as-detail-label { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .6px; color: var(--text-muted); width: 44px; flex-shrink: 0; padding-top: 2px; }
      .as-error-msg { font-size: 11px; color: var(--clr-danger); font-family: 'JetBrains Mono', monospace; }
      .as-params-pre { font-size: 10px; color: var(--text-secondary); background: var(--bg-elevated); border: 1px solid var(--border-default); border-radius: var(--r-sm); padding: 8px 10px; overflow-x: auto; flex: 1; }

      .as-summary-bar {
        display: flex; gap: 0;
        border-bottom: 1px solid var(--border-subtle);
      }
      .as-summary-item {
        flex: 1; padding: 9px 16px;
        border-right: 1px solid var(--border-subtle);
        display: flex; flex-direction: column; gap: 2px;
      }
      .as-summary-item:last-child { border-right: none; }
      .as-summary-label { font-size: 9.5px; font-weight: 600; text-transform: uppercase; letter-spacing: .8px; color: var(--text-muted); }
      .as-summary-val { font-family: 'JetBrains Mono', monospace; font-size: 16px; font-weight: 500; color: var(--text-primary); }
    `;
    document.head.appendChild(s);
  }

  // ── Build shells ─────────────────────────────────────────────────────────

  function buildShell(mount, compact) {
    const maxH = compact ? "280px" : "calc(100vh - 200px)";
    mount.innerHTML = `
      <div class="card">
        <div class="card-header">
          <span class="card-title">Action Stream</span>
          <span class="card-subtitle" id="${compact ? "as-live-badge" : "as-full-live-badge"}">
            <span class="running-dot" style="margin-right:4px"></span>live
          </span>
          <div class="card-actions">
            ${!compact ? `
              <select class="filter-select" id="as-filter-status" onchange="ActionStream.applyFilters()">
                <option value="all">All statuses</option>
                <option value="success">Success</option>
                <option value="failed">Failed</option>
                <option value="running">Running</option>
                <option value="pending">Pending</option>
              </select>
              <select class="filter-select" id="as-filter-type" onchange="ActionStream.applyFilters()">
                <option value="all">All types</option>
                <option value="file">File</option>
                <option value="shell">Shell</option>
                <option value="web">Web</option>
                <option value="ui">UI</option>
                <option value="api">API</option>
              </select>
            ` : ""}
            <button class="card-btn" onclick="ActionStream.loadHistory()">Refresh</button>
            <button class="card-btn card-btn--danger" onclick="ActionStream.clearHistory()">Clear</button>
          </div>
        </div>
        ${!compact ? `
          <div class="as-summary-bar" id="as-summary-bar">
            <div class="as-summary-item"><span class="as-summary-label">Total</span><span class="as-summary-val" id="as-sum-total">—</span></div>
            <div class="as-summary-item"><span class="as-summary-label">Success</span><span class="as-summary-val text-success" id="as-sum-success">—</span></div>
            <div class="as-summary-item"><span class="as-summary-label">Failed</span><span class="as-summary-val text-danger" id="as-sum-failed">—</span></div>
            <div class="as-summary-item"><span class="as-summary-label">Running</span><span class="as-summary-val text-info" id="as-sum-running">—</span></div>
          </div>
        ` : ""}
        <div class="as-list" id="${compact ? "as-list-compact" : "as-list-full"}" style="max-height:${maxH};overflow-y:auto;">
          <div class="empty-state">
            <div class="empty-state-icon">⟳</div>
            <div class="empty-state-text">Loading actions…</div>
          </div>
        </div>
      </div>
    `;
  }

  // ── Render ───────────────────────────────────────────────────────────────

  function getListEl(compact) {
    return document.getElementById(compact ? "as-list-compact" : "as-list-full");
  }

  function renderList(items, compact) {
    const el = getListEl(compact);
    if (!el) return;

    const filtered = filterItems(items);
    if (filtered.length === 0) {
      el.innerHTML = `<div class="empty-state"><div class="empty-state-icon">○</div><div class="empty-state-text">No actions match the current filter.</div></div>`;
      return;
    }

    el.innerHTML = `<div class="as-list">${filtered.map(i => renderItem(i, compact)).join("")}</div>`;
  }

  function filterItems(items) {
    return items.filter(item => {
      const s = (item.status || "").toLowerCase();
      const t = (item.tool || item.type || item.source || "").toLowerCase();
      const statusOk = _filter.status === "all" ||
        (_filter.status === "success" && (s === "success" || s === "done")) ||
        (_filter.status === "failed"  && (s === "failed"  || s === "error")) ||
        (_filter.status === "running" && (s === "running" || s === "active")) ||
        (_filter.status === "pending" && (s === "pending" || s === "queued"));
      const typeOk   = _filter.type === "all" || t.includes(_filter.type);
      return statusOk && typeOk;
    });
  }

  // ── Prepend a live item ──────────────────────────────────────────────────

  function prependItem(item) {
    // Update in memory
    const existingIdx = _items.findIndex(i => (i.task_id || i.id) === (item.task_id || item.id));
    if (existingIdx >= 0) {
      _items[existingIdx] = { ..._items[existingIdx], ...item };
    } else {
      _items.unshift(item);
      if (_items.length > MAX_LIVE) _items.pop();
    }

    // Re-render both mounts if they exist
    if (document.getElementById("as-list-compact")) renderList(_items, true);
    if (document.getElementById("as-list-full"))    renderList(_items, false);
  }

  // ── Summary bar ──────────────────────────────────────────────────────────

  function renderSummary(data) {
    const set = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val ?? "—";
    };
    set("as-sum-total",   data.total);
    set("as-sum-success", data.by_status?.success || data.by_status?.done || 0);
    set("as-sum-failed",  data.by_status?.failed  || data.by_status?.error || 0);
    set("as-sum-running", data.by_status?.running || data.by_status?.active || 0);
  }

  // ── Public API ───────────────────────────────────────────────────────────

  window.ActionStream = {
    async loadHistory() {
      const { ok, data } = await App.apiFetch("/api/actions/history?limit=100");
      if (ok && data?.actions) {
        _items = data.actions;
        if (document.getElementById("as-list-compact")) renderList(_items, true);
        if (document.getElementById("as-list-full"))    renderList(_items, false);
      }

      // Load summary for full view
      if (document.getElementById("as-sum-total")) {
        const { ok: sok, data: sdata } = await App.apiFetch("/api/actions/summary");
        if (sok && sdata) renderSummary(sdata);
      }
    },

    applyFilters() {
      const s = document.getElementById("as-filter-status");
      const t = document.getElementById("as-filter-type");
      if (s) _filter.status = s.value;
      if (t) _filter.type   = t.value;
      renderList(_items, false);
    },

    toggleExpand(id) {
      if (!id) return;
      if (_expanded.has(id)) _expanded.delete(id);
      else                   _expanded.add(id);
      if (document.getElementById("as-list-compact")) renderList(_items, true);
      if (document.getElementById("as-list-full"))    renderList(_items, false);
    },

    async clearHistory() {
      if (!confirm("Clear all action history?")) return;
      const { ok, error } = await App.apiFetch("/api/actions/clear", { method: "DELETE" });
      if (ok) {
        _items = [];
        if (document.getElementById("as-list-compact")) renderList(_items, true);
        if (document.getElementById("as-list-full"))    renderList(_items, false);
        App.toast.show("success", "Actions Cleared", "Action history has been cleared.");
      } else {
        App.toast.show("error", "Clear Failed", error);
      }
    },
  };

  // ── Component init — deferred ────────────────────────────────────────────

  document.addEventListener("DOMContentLoaded", function () {
    injectStyles();

    const compact = document.getElementById("actionStreamMount");
    const full    = document.getElementById("actionStreamFullMount");

    if (compact) buildShell(compact, true);
    if (full)    buildShell(full,    false);

    // Initial data load
    ActionStream.loadHistory();

    // WS live updates
    App.ws.on("action_started", (data) => {
      prependItem({ ...data, status: "running", ts: data.timestamp || new Date().toISOString() });
    });

    App.ws.on("action_taken", (data) => {
      prependItem({ ...data, ts: data.timestamp || new Date().toISOString() });
    });

    App.ws.on("action_complete", (data) => {
      prependItem({ ...data, status: "success", ts: data.timestamp || new Date().toISOString() });
    });

    App.ws.on("action_failed", (data) => {
      prependItem({ ...data, status: "failed", ts: data.timestamp || new Date().toISOString() });
    });

    App.ws.on("task_complete", (data) => {
      if (data.task_id) prependItem({ ...data, status: "success", ts: new Date().toISOString() });
    });

    App.ws.on("task_failed", (data) => {
      if (data.task_id) prependItem({ ...data, status: "failed", ts: new Date().toISOString() });
    });
  });

}());