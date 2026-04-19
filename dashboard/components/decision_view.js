/**
 * decision_view.js
 *
 * Renders into: #decisionViewMount      (overview, compact)
 *               #decisionViewFullMount  (full Decisions page)
 *
 * Data sources:
 *   WS  intent_parsed        → update intent + confidence
 *   WS  plan_created         → update plan steps
 *   WS  decision_made        → update capability/tool selected
 *   WS  confirmation_required → safety gate display (intercepted by App.safety)
 *   WS  confirmation_response → show approved/denied result
 *   WS  user_input_received   → update raw input
 *
 * No REST polling — purely event-driven from the EventBus via WS.
 */

(function () {
  "use strict";

  // ── State ────────────────────────────────────────────────────────────────

  const HISTORY_MAX = 20;
  let _current = {
    raw_input:   null,
    intent:      null,
    confidence:  null,
    plan:        null,         // array of step strings
    tool:        null,
    capability:  null,
    safety:      null,         // { required: bool, approved: bool|null, risk: string }
    ts:          null,
  };
  let _history = [];           // past decisions, newest first

  // ── Scoped styles ────────────────────────────────────────────────────────

  function injectStyles() {
    if (document.getElementById("dv-styles")) return;
    const s = document.createElement("style");
    s.id = "dv-styles";
    s.textContent = `
      .dv-flow { display: flex; flex-direction: column; gap: 0; }

      .dv-step {
        display: flex; gap: 0; align-items: stretch;
        padding: 0 16px;
      }
      .dv-step-line {
        display: flex; flex-direction: column; align-items: center;
        width: 22px; flex-shrink: 0; padding: 14px 0 0;
      }
      .dv-step-dot {
        width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
        border: 2px solid var(--border-strong);
        background: var(--bg-surface);
        transition: border-color var(--t-base), background var(--t-base);
      }
      .dv-step-dot.done   { background: var(--clr-success); border-color: var(--clr-success); }
      .dv-step-dot.active { background: var(--clr-accent);  border-color: var(--clr-accent); box-shadow: 0 0 6px var(--clr-accent-glow); }
      .dv-step-dot.warn   { background: var(--clr-warn);    border-color: var(--clr-warn); }
      .dv-step-dot.danger { background: var(--clr-danger);  border-color: var(--clr-danger); }
      .dv-step-connector {
        width: 1px; flex: 1; min-height: 14px;
        background: var(--border-subtle); margin-top: 4px;
      }
      .dv-step-body {
        flex: 1; padding: 12px 0 12px 12px;
        border-bottom: 1px solid var(--border-subtle);
        min-width: 0;
      }
      .dv-step:last-child .dv-step-body { border-bottom: none; }

      .dv-step-label {
        font-size: 9.5px; font-weight: 700; letter-spacing: 1px;
        text-transform: uppercase; color: var(--text-muted); margin-bottom: 5px;
      }
      .dv-step-value {
        font-size: 12px; color: var(--text-primary);
        word-break: break-word; line-height: 1.5;
      }
      .dv-step-value.mono {
        font-family: 'JetBrains Mono', monospace; font-size: 11px;
      }
      .dv-step-value.intent { color: var(--clr-accent); }
      .dv-step-value.empty  { color: var(--text-muted); font-style: italic; }

      .dv-confidence-row {
        display: flex; align-items: center; gap: 8px; margin-top: 5px;
      }
      .dv-confidence-label { font-size: 10px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace; }
      .dv-confidence-bar { flex: 1; height: 3px; background: var(--border-subtle); border-radius: 2px; overflow: hidden; }
      .dv-confidence-fill { height: 100%; background: var(--clr-accent); border-radius: 2px; transition: width .5s ease; }

      .dv-plan-list { display: flex; flex-direction: column; gap: 4px; margin-top: 4px; }
      .dv-plan-step {
        display: flex; align-items: center; gap: 7px;
        font-size: 11px; color: var(--text-secondary);
      }
      .dv-plan-num {
        width: 17px; height: 17px; border-radius: 50%; flex-shrink: 0;
        background: var(--bg-elevated); border: 1px solid var(--border-default);
        display: flex; align-items: center; justify-content: center;
        font-size: 9px; font-weight: 700; color: var(--text-muted);
        font-family: 'JetBrains Mono', monospace;
      }
      .dv-plan-text { font-family: 'JetBrains Mono', monospace; font-size: 11px; }

      .dv-safety-chips { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }

      .dv-ts { font-family: 'JetBrains Mono', monospace; font-size: 10px; color: var(--text-muted); margin-top: 4px; }

      .dv-history { border-top: 1px solid var(--border-subtle); }
      .dv-history-header {
        display: flex; align-items: center; justify-content: space-between;
        padding: 9px 16px;
        font-size: 10px; font-weight: 700; letter-spacing: .8px;
        text-transform: uppercase; color: var(--text-muted);
        cursor: pointer; user-select: none;
      }
      .dv-history-header:hover { background: var(--bg-elevated); }
      .dv-history-list { display: none; flex-direction: column; gap: 0; }
      .dv-history-list.open { display: flex; }
      .dv-history-item {
        display: flex; align-items: center; gap: 10px;
        padding: 8px 16px; border-top: 1px solid var(--border-subtle);
        cursor: pointer; transition: background var(--t-fast);
        font-size: 11px;
      }
      .dv-history-item:hover { background: var(--bg-elevated); }
      .dv-history-intent { flex: 1; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .dv-history-ts { color: var(--text-muted); font-family: 'JetBrains Mono', monospace; font-size: 10px; flex-shrink: 0; }
    `;
    document.head.appendChild(s);
  }

  // ── Render ───────────────────────────────────────────────────────────────

  function safetyContent(safety) {
    if (!safety) return `<span class="chip chip--muted">No gate triggered</span>`;
    const chips = [];
    if (safety.approved === true)  chips.push(`<span class="chip chip--success">✓ Approved</span>`);
    if (safety.approved === false) chips.push(`<span class="chip chip--danger">✕ Denied</span>`);
    if (safety.required && safety.approved === null) chips.push(`<span class="chip chip--warn">⏳ Awaiting confirmation</span>`);
    if (safety.risk)               chips.push(`<span class="chip chip--warn">risk: ${App.esc(safety.risk)}</span>`);
    return chips.join("") || `<span class="chip chip--muted">No gate triggered</span>`;
  }

  function renderFlow(el, d) {
    const confPct = d.confidence != null ? Math.round(d.confidence * 100) : null;
    const planSteps = Array.isArray(d.plan) ? d.plan : [];

    const dotClass = (val) => {
      if (!val) return "";
      return "done";
    };
    const safetyDotClass = () => {
      if (!d.safety) return "";
      if (d.safety.approved === true)  return "done";
      if (d.safety.approved === false) return "danger";
      if (d.safety.required)           return "warn";
      return "";
    };

    el.innerHTML = `
      <div class="dv-flow">

        <div class="dv-step">
          <div class="dv-step-line">
            <div class="dv-step-dot ${dotClass(d.raw_input)}"></div>
            <div class="dv-step-connector"></div>
          </div>
          <div class="dv-step-body">
            <div class="dv-step-label">Raw Input</div>
            <div class="dv-step-value ${d.raw_input ? "" : "empty"}">
              ${d.raw_input ? App.esc(d.raw_input) : "Waiting for input…"}
            </div>
            ${d.ts ? `<div class="dv-ts">${App.fmtTime(d.ts)}</div>` : ""}
          </div>
        </div>

        <div class="dv-step">
          <div class="dv-step-line">
            <div class="dv-step-dot ${dotClass(d.intent)}"></div>
            <div class="dv-step-connector"></div>
          </div>
          <div class="dv-step-body">
            <div class="dv-step-label">Parsed Intent</div>
            <div class="dv-step-value intent mono ${d.intent ? "" : "empty"}">
              ${d.intent ? App.esc(d.intent) : "—"}
            </div>
            ${confPct != null ? `
              <div class="dv-confidence-row">
                <span class="dv-confidence-label">${confPct}%</span>
                <div class="dv-confidence-bar">
                  <div class="dv-confidence-fill" style="width:${confPct}%"></div>
                </div>
              </div>
            ` : ""}
          </div>
        </div>

        <div class="dv-step">
          <div class="dv-step-line">
            <div class="dv-step-dot ${planSteps.length ? "done" : ""}"></div>
            <div class="dv-step-connector"></div>
          </div>
          <div class="dv-step-body">
            <div class="dv-step-label">Plan ${planSteps.length ? `(${planSteps.length} steps)` : ""}</div>
            ${planSteps.length ? `
              <div class="dv-plan-list">
                ${planSteps.map((step, i) => `
                  <div class="dv-plan-step">
                    <div class="dv-plan-num">${i + 1}</div>
                    <span class="dv-plan-text">${App.esc(String(step))}</span>
                  </div>
                `).join("")}
              </div>
            ` : `<div class="dv-step-value empty">No plan generated yet</div>`}
          </div>
        </div>

        <div class="dv-step">
          <div class="dv-step-line">
            <div class="dv-step-dot ${dotClass(d.tool)}"></div>
            <div class="dv-step-connector"></div>
          </div>
          <div class="dv-step-body">
            <div class="dv-step-label">Tool Selected</div>
            <div class="dv-step-value mono ${d.tool ? "" : "empty"}">
              ${d.tool ? App.esc(d.tool) : "—"}
              ${d.capability ? ` <span style="color:var(--text-muted)">via ${App.esc(d.capability)}</span>` : ""}
            </div>
          </div>
        </div>

        <div class="dv-step">
          <div class="dv-step-line">
            <div class="dv-step-dot ${safetyDotClass()}"></div>
          </div>
          <div class="dv-step-body">
            <div class="dv-step-label">Safety Gate</div>
            <div class="dv-safety-chips">${safetyContent(d.safety)}</div>
          </div>
        </div>

      </div>
    `;
  }

  function renderHistory(el) {
    const list = el.querySelector(".dv-history-list");
    if (!list) return;
    if (_history.length === 0) {
      list.innerHTML = `<div class="dv-history-item"><span class="dv-history-intent text-muted">No history yet</span></div>`;
      return;
    }
    list.innerHTML = _history.map((h, i) => `
      <div class="dv-history-item" onclick="DecisionView.loadHistoryItem(${i})">
        <span class="chip ${h.intent ? "chip--info" : "chip--muted"}" style="flex-shrink:0">${App.esc(h.intent || "unknown")}</span>
        <span class="dv-history-intent">${App.esc(h.raw_input || "—")}</span>
        <span class="dv-history-ts">${App.fmtTime(h.ts)}</span>
      </div>
    `).join("");
  }

  // ── Build shell ──────────────────────────────────────────────────────────

  function buildShell(mount) {
    mount.innerHTML = `
      <div class="card">
        <div class="card-header">
          <span class="card-title">Decision View</span>
          <span class="card-subtitle" id="dv-last-ts">—</span>
          <div class="card-actions">
            <button class="card-btn" onclick="DecisionView.clearCurrent()">Clear</button>
          </div>
        </div>
        <div id="dv-flow-container"></div>
        <div class="dv-history" id="dv-history-section">
          <div class="dv-history-header" onclick="DecisionView.toggleHistory()">
            Past Decisions
            <span id="dv-history-toggle">▼</span>
          </div>
          <div class="dv-history-list" id="dv-history-list"></div>
        </div>
      </div>
    `;
  }

  function refresh() {
    const container = document.getElementById("dv-flow-container");
    if (container) renderFlow(container, _current);

    const lastTs = document.getElementById("dv-last-ts");
    if (lastTs) lastTs.textContent = _current.ts ? App.fmtTime(_current.ts) : "waiting…";

    // Render history list if open
    const list = document.getElementById("dv-history-list");
    if (list && list.classList.contains("open")) {
      const histSection = document.getElementById("dv-history-section");
      if (histSection) renderHistory(histSection);
    }
  }

  function archiveCurrent() {
    if (!_current.raw_input && !_current.intent) return;
    _history.unshift({ ..._current });
    if (_history.length > HISTORY_MAX) _history.pop();
  }

  // ── Public API ───────────────────────────────────────────────────────────

  window.DecisionView = {
    toggleHistory() {
      const list = document.getElementById("dv-history-list");
      const tog  = document.getElementById("dv-history-toggle");
      const histSection = document.getElementById("dv-history-section");
      if (!list) return;
      const open = list.classList.toggle("open");
      if (tog) tog.textContent = open ? "▲" : "▼";
      if (open && histSection) renderHistory(histSection);
    },

    loadHistoryItem(idx) {
      const item = _history[idx];
      if (!item) return;
      _current = { ...item };
      refresh();
    },

    clearCurrent() {
      archiveCurrent();
      _current = { raw_input: null, intent: null, confidence: null, plan: null, tool: null, capability: null, safety: null, ts: null };
      refresh();
    },
  };

  // ── Component init ───────────────────────────────────────────────────────

  document.addEventListener("DOMContentLoaded", function () {
    injectStyles();

    const compact = document.getElementById("decisionViewMount");
    const full    = document.getElementById("decisionViewFullMount");

    if (compact) buildShell(compact);
    if (full)    buildShell(full);

    refresh();

    // WS event handlers
    App.ws.on("user_input_received", (data) => {
      archiveCurrent();
      _current = { raw_input: data.input || data.text || data.query, intent: null, confidence: null, plan: null, tool: null, capability: null, safety: null, ts: data.timestamp || new Date().toISOString() };
      refresh();
    });

    App.ws.on("intent_parsed", (data) => {
      _current.intent     = data.intent || data.intent_type || data.action;
      _current.confidence = data.confidence ?? data.score ?? null;
      _current.ts         = data.timestamp || _current.ts || new Date().toISOString();
      refresh();
    });

    App.ws.on("plan_created", (data) => {
      const steps = data.steps || data.plan || data.actions || [];
      _current.plan = Array.isArray(steps)
        ? steps.map(s => (typeof s === "string" ? s : s.tool || s.action || JSON.stringify(s)))
        : [String(steps)];
      _current.ts = data.timestamp || _current.ts;
      refresh();
    });

    App.ws.on("decision_made", (data) => {
      _current.tool       = data.tool || data.tool_name;
      _current.capability = data.capability || data.capability_type;
      _current.ts         = data.timestamp || _current.ts;
      refresh();
    });

    App.ws.on("confirmation_required", (data) => {
      _current.safety = {
        required: true,
        approved: null,
        risk:     data.risk || data.risk_level || "medium",
      };
      refresh();
    });

    App.ws.on("confirmation_response", (data) => {
      if (_current.safety) {
        _current.safety.approved = data.approved;
      }
      refresh();
    });

    App.ws.on("task_complete", (data) => {
      if (_current.safety && _current.safety.approved === null) {
        _current.safety.approved = true;
      }
      refresh();
    });
  });

}());