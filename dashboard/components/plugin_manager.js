/**
 * plugin_manager.js
 *
 * Renders into: #pluginManagerMount  (full Plugins page)
 *
 * Data sources:
 *   GET    /api/plugins              → list all plugins
 *   GET    /api/plugins/{name}       → get single plugin manifest
 *   POST   /api/plugins/{name}/enable
 *   POST   /api/plugins/{name}/disable
 *   POST   /api/plugins/{name}/reload
 *   POST   /api/plugins/reload-all
 *   POST   /api/plugins/generate     → AI scaffold a new plugin
 *   DELETE /api/plugins/{name}       → remove plugin
 *
 *   WS  plugin_loaded / plugin_unloaded / plugin_error → live status updates
 */

(function () {
  "use strict";

  // ── State ────────────────────────────────────────────────────────────────

  let _plugins       = [];     // full list from API
  let _selected      = null;   // name of expanded plugin
  let _generating    = false;
  let _genFormOpen   = false;

  // ── Scoped styles ────────────────────────────────────────────────────────

  function injectStyles() {
    if (document.getElementById("pm-styles")) return;
    const s = document.createElement("style");
    s.id = "pm-styles";
    s.textContent = `
      .pm-layout { display: grid; grid-template-columns: 1fr 340px; gap: 14px; }
      @media (max-width: 1000px) { .pm-layout { grid-template-columns: 1fr; } }

      /* Plugin list */
      .pm-row {
        display: flex; align-items: center; gap: 12px;
        padding: 11px 16px;
        border-bottom: 1px solid var(--border-subtle);
        cursor: pointer; transition: background var(--t-fast);
      }
      .pm-row:last-child { border-bottom: none; }
      .pm-row:hover { background: var(--bg-elevated); }
      .pm-row.selected { background: var(--clr-accent-dim); }

      .pm-icon {
        width: 32px; height: 32px; border-radius: var(--r-md); flex-shrink: 0;
        background: var(--bg-elevated); border: 1px solid var(--border-default);
        display: flex; align-items: center; justify-content: center;
        font-size: 15px; color: var(--text-secondary);
      }
      .pm-icon.enabled  { background: var(--clr-accent-dim);  border-color: var(--clr-accent-glow); color: var(--clr-accent); }
      .pm-icon.disabled { opacity: .45; }
      .pm-icon.error    { background: var(--clr-danger-dim);   border-color: var(--clr-danger); }

      .pm-info { flex: 1; min-width: 0; }
      .pm-name { font-size: 12.5px; font-weight: 600; color: var(--text-primary); font-family: 'JetBrains Mono', monospace; }
      .pm-desc { font-size: 11px; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }
      .pm-caps { display: flex; gap: 4px; margin-top: 4px; flex-wrap: wrap; }
      .pm-cap-tag { font-size: 9px; padding: 1px 6px; border-radius: 3px; background: var(--bg-overlay); border: 1px solid var(--border-default); color: var(--text-muted); }

      .pm-controls { display: flex; align-items: center; gap: 7px; flex-shrink: 0; }
      .pm-ver { font-family: 'JetBrains Mono', monospace; font-size: 9.5px; color: var(--text-muted); }
      .pm-action-btn {
        padding: 3px 8px; border-radius: var(--r-sm); font-size: 10px; font-weight: 500;
        background: var(--bg-elevated); border: 1px solid var(--border-default);
        color: var(--text-secondary); transition: all var(--t-fast);
      }
      .pm-action-btn:hover { background: var(--bg-overlay); color: var(--text-primary); border-color: var(--border-strong); }
      .pm-action-btn.danger:hover { background: var(--clr-danger-dim); color: var(--clr-danger); border-color: var(--clr-danger); }

      /* Toggle switch sizes for plugin list */
      .pm-toggle { position: relative; width: 32px; height: 18px; flex-shrink: 0; }
      .pm-toggle input { opacity: 0; width: 0; height: 0; position: absolute; }
      .pm-track {
        position: absolute; inset: 0; border-radius: 9px;
        background: var(--border-strong); cursor: pointer;
        transition: background var(--t-base);
      }
      .pm-thumb {
        position: absolute; top: 2px; left: 2px;
        width: 14px; height: 14px; border-radius: 50%;
        background: white; transition: left var(--t-base); pointer-events: none;
      }
      .pm-toggle input:checked ~ .pm-track { background: var(--clr-accent); }
      .pm-toggle input:checked ~ .pm-thumb { left: 16px; }

      /* Detail panel */
      .pm-detail-card { position: sticky; top: 0; }
      .pm-detail-empty {
        display: flex; flex-direction: column; align-items: center;
        justify-content: center; padding: 40px 20px; gap: 8px;
        color: var(--text-muted); font-size: 12px;
      }
      .pm-manifest-pre {
        font-family: 'JetBrains Mono', monospace; font-size: 10.5px;
        color: var(--text-secondary); background: var(--bg-elevated);
        border: 1px solid var(--border-default); border-radius: var(--r-md);
        padding: 12px; overflow: auto; max-height: 280px; margin: 12px 16px;
      }
      .pm-detail-section { padding: 12px 16px; border-top: 1px solid var(--border-subtle); }
      .pm-detail-section-title { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .8px; color: var(--text-muted); margin-bottom: 8px; }
      .pm-detail-actions { display: flex; flex-wrap: wrap; gap: 6px; }

      /* Generator form */
      .pm-gen-form { padding: 14px 16px; border-top: 1px solid var(--border-subtle); display: none; }
      .pm-gen-form.open { display: block; }
      .pm-field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 10px; }
      .pm-field-label { font-size: 10.5px; font-weight: 600; text-transform: uppercase; letter-spacing: .6px; color: var(--text-muted); }
      .pm-field input, .pm-field textarea, .pm-field select {
        padding: 7px 10px; border-radius: var(--r-md);
        background: var(--bg-elevated); border: 1px solid var(--border-default);
        color: var(--text-primary); font-size: 12px; font-family: inherit;
        transition: border-color var(--t-fast);
      }
      .pm-field input:focus, .pm-field textarea:focus {
        outline: none; border-color: var(--clr-accent);
      }
      .pm-field textarea { resize: vertical; min-height: 64px; }
      .pm-gen-submit {
        width: 100%; padding: 9px; border-radius: var(--r-md);
        background: var(--clr-accent-dim); color: var(--clr-accent);
        border: 1px solid var(--clr-accent-glow); font-size: 12px; font-weight: 600;
        transition: all var(--t-fast); font-family: inherit;
      }
      .pm-gen-submit:hover:not(:disabled) { background: var(--clr-accent); color: var(--text-inverse); }
      .pm-gen-submit:disabled { opacity: .5; cursor: not-allowed; }

      .pm-gen-trigger {
        display: flex; align-items: center; justify-content: center; gap: 8px;
        width: calc(100% - 32px); margin: 10px 16px;
        padding: 9px; border-radius: var(--r-md);
        background: transparent; border: 1px dashed var(--border-strong);
        color: var(--text-muted); font-size: 11.5px; font-weight: 500;
        transition: all var(--t-fast); font-family: inherit;
      }
      .pm-gen-trigger:hover { border-color: var(--clr-accent); color: var(--clr-accent); background: var(--clr-accent-dim); }
    `;
    document.head.appendChild(s);
  }

  // ── Renderers ────────────────────────────────────────────────────────────

  function iconForPlugin(plugin) {
    const name = (plugin.name || "").toLowerCase();
    const caps = (plugin.capabilities || []).map(c => c.toLowerCase());
    if (caps.includes("web") || name.includes("search")) return "🔍";
    if (caps.includes("file") || name.includes("file"))  return "📁";
    if (caps.includes("shell") || name.includes("shell")) return "💻";
    if (caps.includes("ui") || name.includes("ui"))       return "🖥";
    if (caps.includes("llm") || name.includes("ollama")) return "🧠";
    if (caps.includes("api") || name.includes("api"))     return "🔌";
    return "⚡";
  }

  function renderPluginRow(plugin) {
    const enabled  = plugin.enabled && plugin.loaded;
    const hasError = !!plugin.error;
    const iconCls  = hasError ? "error" : enabled ? "enabled" : "disabled";
    const selected = _selected === plugin.name;
    const caps     = (plugin.capabilities || []).slice(0, 5);

    return `
      <div class="pm-row ${selected ? "selected" : ""}"
           onclick="PluginManager.selectPlugin('${App.esc(plugin.name)}')">
        <div class="pm-icon ${iconCls}">${iconForPlugin(plugin)}</div>
        <div class="pm-info">
          <div class="pm-name">${App.esc(plugin.name)}</div>
          <div class="pm-desc">${App.esc(plugin.description || "No description")}</div>
          <div class="pm-caps">
            ${caps.map(c => `<span class="pm-cap-tag">${App.esc(c)}</span>`).join("")}
          </div>
        </div>
        <div class="pm-controls" onclick="event.stopPropagation()">
          <span class="pm-ver">v${App.esc(plugin.version || "?")}</span>
          <span class="chip ${hasError ? "chip--danger" : enabled ? "chip--success" : "chip--muted"}">
            ${hasError ? "error" : enabled ? "enabled" : "disabled"}
          </span>
          <label class="pm-toggle" title="${enabled ? "Disable" : "Enable"} ${plugin.name}">
            <input type="checkbox" ${plugin.enabled ? "checked" : ""}
                   onchange="PluginManager.togglePlugin('${App.esc(plugin.name)}', this.checked)">
            <div class="pm-track"></div>
            <div class="pm-thumb"></div>
          </label>
        </div>
      </div>
    `;
  }

  function renderDetail(plugin) {
    const detailBody = document.getElementById("pm-detail-body");
    if (!detailBody) return;

    if (!plugin) {
      detailBody.innerHTML = `<div class="pm-detail-empty"><div style="font-size:22px;opacity:.3">⚡</div>Select a plugin to view details</div>`;
      return;
    }

    const manifestStr = JSON.stringify({
      name:         plugin.name,
      version:      plugin.version,
      description:  plugin.description,
      capabilities: plugin.capabilities,
      enabled:      plugin.enabled,
      loaded:       plugin.loaded,
      ...(plugin.parameters ? { parameters: plugin.parameters } : {}),
    }, null, 2);

    detailBody.innerHTML = `
      <div style="padding: 14px 16px 10px; border-bottom: 1px solid var(--border-subtle);">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
          <div class="pm-icon ${plugin.enabled ? "enabled" : "disabled"}" style="width:36px;height:36px;font-size:17px">${iconForPlugin(plugin)}</div>
          <div>
            <div style="font-size:13px;font-weight:700;color:var(--text-primary);font-family:'JetBrains Mono',monospace">${App.esc(plugin.name)}</div>
            <div style="font-size:11px;color:var(--text-muted)">v${App.esc(plugin.version || "?")} · ${plugin.loaded ? "loaded" : "not loaded"}</div>
          </div>
        </div>
        <p style="font-size:12px;color:var(--text-secondary);line-height:1.6">${App.esc(plugin.description || "No description provided.")}</p>
      </div>
      <pre class="pm-manifest-pre">${App.esc(manifestStr)}</pre>
      <div class="pm-detail-section">
        <div class="pm-detail-section-title">Actions</div>
        <div class="pm-detail-actions">
          <button class="card-btn" onclick="PluginManager.reloadPlugin('${App.esc(plugin.name)}')">
            ↺ Reload
          </button>
          <button class="card-btn" onclick="PluginManager.togglePlugin('${App.esc(plugin.name)}', ${!plugin.enabled})">
            ${plugin.enabled ? "Disable" : "Enable"}
          </button>
          <button class="card-btn card-btn--danger" onclick="PluginManager.removePlugin('${App.esc(plugin.name)}')">
            Delete
          </button>
        </div>
      </div>
      ${plugin.error ? `
        <div class="pm-detail-section">
          <div class="pm-detail-section-title">Error</div>
          <div style="font-family:'JetBrains Mono',monospace;font-size:10.5px;color:var(--clr-danger);background:var(--clr-danger-dim);padding:8px 10px;border-radius:var(--r-sm);">${App.esc(plugin.error)}</div>
        </div>
      ` : ""}
    `;
  }

  // ── Build shell ──────────────────────────────────────────────────────────

  function buildShell(mount) {
    mount.innerHTML = `
      <div class="pm-layout">
        <!-- Left: plugin list -->
        <div class="card">
          <div class="card-header">
            <span class="card-title">Plugins</span>
            <span class="card-subtitle" id="pm-count-label">loading…</span>
            <div class="card-actions">
              <button class="card-btn" onclick="PluginManager.reloadAll()">↺ Reload All</button>
              <button class="card-btn" onclick="PluginManager.loadPlugins()">Refresh</button>
            </div>
          </div>
          <div id="pm-list">
            <div class="empty-state"><div class="empty-state-text">Loading plugins…</div></div>
          </div>
          <!-- Generator toggle -->
          <button class="pm-gen-trigger" onclick="PluginManager.toggleGenForm()">
            + Generate New Plugin with AI
          </button>
          <!-- Generator form (hidden by default) -->
          <div class="pm-gen-form" id="pm-gen-form">
            <div class="pm-detail-section-title" style="margin-bottom:12px">Generate Plugin with AI</div>
            <div class="pm-field">
              <label class="pm-field-label">Plugin Name (snake_case)</label>
              <input type="text" id="gen-name" placeholder="e.g. weather_lookup" />
            </div>
            <div class="pm-field">
              <label class="pm-field-label">Description</label>
              <textarea id="gen-desc" placeholder="What should this plugin do?"></textarea>
            </div>
            <div class="pm-field">
              <label class="pm-field-label">Capabilities (comma-separated)</label>
              <input type="text" id="gen-caps" placeholder="e.g. web, api" />
            </div>
            <button class="pm-gen-submit" id="gen-submit-btn" onclick="PluginManager.generatePlugin()">
              Generate Plugin
            </button>
          </div>
        </div>

        <!-- Right: detail panel -->
        <div class="card pm-detail-card">
          <div class="card-header">
            <span class="card-title">Plugin Detail</span>
          </div>
          <div id="pm-detail-body">
            <div class="pm-detail-empty">
              <div style="font-size:22px;opacity:.3">⚡</div>
              Select a plugin to view details
            </div>
          </div>
        </div>
      </div>
    `;
  }

  // ── Public API ───────────────────────────────────────────────────────────

  window.PluginManager = {
    async loadPlugins() {
      const { ok, data } = await App.apiFetch("/api/plugins");
      if (!ok || !data) return;
      _plugins = data.plugins || [];

      const listEl = document.getElementById("pm-list");
      if (listEl) {
        if (_plugins.length === 0) {
          listEl.innerHTML = `<div class="empty-state"><div class="empty-state-text">No plugins discovered.</div></div>`;
        } else {
          listEl.innerHTML = _plugins.map(renderPluginRow).join("");
        }
      }

      const label = document.getElementById("pm-count-label");
      if (label) {
        const enabled = _plugins.filter(p => p.enabled && p.loaded).length;
        label.textContent = `${_plugins.length} total · ${enabled} enabled`;
      }

      // Update nav badge
      const badge = document.getElementById("pluginCountBadge");
      if (badge) badge.textContent = _plugins.length;

      // Refresh detail if a plugin is selected
      if (_selected) {
        const found = _plugins.find(p => p.name === _selected);
        renderDetail(found || null);
      }
    },

    selectPlugin(name) {
      _selected = _selected === name ? null : name;
      const found = _plugins.find(p => p.name === name);
      renderDetail(found || null);
      // Re-render rows to update selected state
      const listEl = document.getElementById("pm-list");
      if (listEl) listEl.innerHTML = _plugins.map(renderPluginRow).join("");
    },

    async togglePlugin(name, enable) {
      const action = enable ? "enable" : "disable";
      const { ok, error } = await App.apiFetch(`/api/plugins/${encodeURIComponent(name)}/${action}`, { method: "POST" });
      if (ok) {
        App.toast.show("success", `Plugin ${enable ? "Enabled" : "Disabled"}`, name);
        await PluginManager.loadPlugins();
      } else {
        App.toast.show("error", "Plugin Action Failed", error);
        // Revert checkbox state by reloading
        await PluginManager.loadPlugins();
      }
    },

    async reloadPlugin(name) {
      const { ok, error } = await App.apiFetch(`/api/plugins/${encodeURIComponent(name)}/reload`, { method: "POST" });
      if (ok) {
        App.toast.show("success", "Plugin Reloaded", name);
        await PluginManager.loadPlugins();
      } else {
        App.toast.show("error", "Reload Failed", error);
      }
    },

    async reloadAll() {
      const { ok, error } = await App.apiFetch("/api/plugins/reload-all", { method: "POST" });
      if (ok) {
        App.toast.show("success", "All Plugins Reloaded", "Hot-reload complete.");
        await PluginManager.loadPlugins();
      } else {
        App.toast.show("error", "Reload All Failed", error);
      }
    },

    async removePlugin(name) {
      if (!confirm(`Permanently delete plugin "${name}"? This cannot be undone.`)) return;
      const { ok, error } = await App.apiFetch(`/api/plugins/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (ok) {
        App.toast.show("success", "Plugin Removed", name);
        if (_selected === name) {
          _selected = null;
          renderDetail(null);
        }
        await PluginManager.loadPlugins();
      } else {
        App.toast.show("error", "Delete Failed", error);
      }
    },

    toggleGenForm() {
      _genFormOpen = !_genFormOpen;
      const form = document.getElementById("pm-gen-form");
      if (form) form.classList.toggle("open", _genFormOpen);
    },

    async generatePlugin() {
      if (_generating) return;

      const name = (document.getElementById("gen-name")?.value || "").trim();
      const desc = (document.getElementById("gen-desc")?.value || "").trim();
      const caps = (document.getElementById("gen-caps")?.value || "")
        .split(",").map(c => c.trim()).filter(Boolean);

      if (!name) { App.toast.show("warn", "Validation", "Plugin name is required."); return; }
      if (!desc) { App.toast.show("warn", "Validation", "Description is required."); return; }

      _generating = true;
      const btn = document.getElementById("gen-submit-btn");
      if (btn) { btn.disabled = true; btn.textContent = "Generating…"; }

      const { ok, data, error } = await App.apiFetch("/api/plugins/generate", {
        method: "POST",
        body:   JSON.stringify({ name, description: desc, capabilities: caps }),
      });

      _generating = false;
      if (btn) { btn.disabled = false; btn.textContent = "Generate Plugin"; }

      if (ok) {
        App.toast.show("success", "Plugin Generated", `${name} scaffolded — ${(data?.files || []).length} files created.`);
        // Clear form
        ["gen-name", "gen-desc", "gen-caps"].forEach(id => {
          const el = document.getElementById(id);
          if (el) el.value = "";
        });
        PluginManager.toggleGenForm();
        await PluginManager.loadPlugins();
      } else {
        App.toast.show("error", "Generation Failed", error);
      }
    },
  };

  // ── Component init ───────────────────────────────────────────────────────

  document.addEventListener("DOMContentLoaded", function () {
    injectStyles();

    const mount = document.getElementById("pluginManagerMount");
    if (!mount) return;

    buildShell(mount);
    PluginManager.loadPlugins();

    // WS live updates
    App.ws.on("plugin_loaded", (data) => {
      App.toast.show("success", "Plugin Loaded", data?.name || "unknown");
      PluginManager.loadPlugins();
    });

    App.ws.on("plugin_unloaded", (data) => {
      App.toast.show("warn", "Plugin Unloaded", data?.name || "unknown");
      PluginManager.loadPlugins();
    });

    App.ws.on("plugin_error", (data) => {
      App.toast.show("error", "Plugin Error", `${data?.name || "unknown"}: ${data?.error || ""}`);
      PluginManager.loadPlugins();
    });
  });

}());