/**
 * dashboard/components/mode_switcher.js
 * ═══════════════════════════════════════
 * Mode-switcher UI component for the Operonix dashboard.
 *
 * Renders a two-button toggle (Voice | Panel) that:
 *   • Reads the current active mode on mount via GET /api/system/input-mode
 *   • Sends POST /api/system/input-mode when the user clicks a button
 *   • Listens on the existing WebSocket for "input_mode_changed" events
 *     so all open dashboard tabs reflect state without polling
 *
 * Usage (in frontend/app.js):
 *   import { ModeSwitcher } from '../components/mode_switcher.js';
 *   const switcher = new ModeSwitcher(document.getElementById('mode-switcher'));
 *   switcher.connect(wsClient);   // pass the existing WebSocket wrapper
 *
 * No hardcoded host/port — reads from window.OPERONIX_API_BASE or falls back
 * to the same origin as the dashboard page.
 *
 * No external dependencies — vanilla JS only.
 */

const _API_BASE = window.OPERONIX_API_BASE || '';

/**
 * Post a mode change request to the API.
 *
 * @param {'voice'|'panel'|'none'} mode
 * @returns {Promise<{mode: string, changed: boolean}>}
 */
async function _requestModeChange(mode) {
  const res = await fetch(`${_API_BASE}/api/system/input-mode`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

/**
 * Fetch the current mode from the API.
 *
 * @returns {Promise<string>}
 */
async function _fetchCurrentMode() {
  const res = await fetch(`${_API_BASE}/api/system/input-mode`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return data.mode || 'none';
}

// ─────────────────────────────────────────────────────────────────────────────
// ModeSwitcher
// ─────────────────────────────────────────────────────────────────────────────

export class ModeSwitcher {
  /**
   * @param {HTMLElement} container  — the element to render into
   */
  constructor(container) {
    this._container = container;
    this._currentMode = 'none';
    this._pending = false;          // true while an API call is in flight
    this._wsUnsubscribe = null;     // cleanup fn returned by connect()

    this._render();
    this._loadInitialState();
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /**
   * Wire up a WebSocket client so the switcher updates automatically when
   * another tab or the API changes the mode.
   *
   * @param {Object} wsClient  — must expose .onEvent(eventType, callback)
   *                             and return an unsubscribe function.
   */
  connect(wsClient) {
    if (!wsClient || typeof wsClient.onEvent !== 'function') {
      console.warn('ModeSwitcher.connect: wsClient must expose onEvent()');
      return;
    }
    this._wsUnsubscribe = wsClient.onEvent(
      'input_mode_changed',
      (data) => this._applyMode(data.new_mode),
    );
  }

  /** Remove WebSocket listener and clear the DOM. */
  destroy() {
    if (typeof this._wsUnsubscribe === 'function') {
      this._wsUnsubscribe();
    }
    this._container.innerHTML = '';
  }

  // ── Rendering ──────────────────────────────────────────────────────────────

  _render() {
    this._container.innerHTML = '';
    this._container.setAttribute('role', 'group');
    this._container.setAttribute('aria-label', 'Input mode');

    this._container.style.cssText = [
      'display:inline-flex',
      'align-items:center',
      'gap:6px',
      'padding:4px',
      'border:0.5px solid var(--color-border-tertiary, rgba(0,0,0,0.15))',
      'border-radius:10px',
      'background:var(--color-background-secondary, #f5f5f3)',
    ].join(';');

    this._voiceBtn = this._makeButton('Voice', 'voice');
    this._panelBtn = this._makeButton('Panel', 'panel');
    this._statusDot = this._makeDot();

    this._container.appendChild(this._voiceBtn);
    this._container.appendChild(this._panelBtn);
    this._container.appendChild(this._statusDot);
  }

  _makeButton(label, mode) {
    const btn = document.createElement('button');
    btn.textContent = label;
    btn.dataset.mode = mode;
    btn.setAttribute('aria-pressed', 'false');
    btn.style.cssText = [
      'padding:5px 14px',
      'border-radius:7px',
      'border:0.5px solid transparent',
      'background:transparent',
      'font-size:13px',
      'font-weight:500',
      'cursor:pointer',
      'transition:background 0.15s,color 0.15s,border-color 0.15s',
      'color:var(--color-text-secondary, #5f5e5a)',
      'font-family:inherit',
    ].join(';');

    btn.addEventListener('click', () => this._onButtonClick(mode));

    btn.addEventListener('mouseenter', () => {
      if (btn.dataset.mode !== this._currentMode) {
        btn.style.background = 'var(--color-background-primary, #ffffff)';
      }
    });
    btn.addEventListener('mouseleave', () => {
      if (btn.dataset.mode !== this._currentMode) {
        btn.style.background = 'transparent';
      }
    });

    return btn;
  }

  _makeDot() {
    const dot = document.createElement('span');
    dot.title = 'Mode status';
    dot.style.cssText = [
      'width:7px',
      'height:7px',
      'border-radius:50%',
      'background:var(--color-border-secondary, rgba(0,0,0,0.3))',
      'display:inline-block',
      'flex-shrink:0',
      'margin-left:2px',
      'transition:background 0.2s',
    ].join(';');
    return dot;
  }

  // ── State application ──────────────────────────────────────────────────────

  _applyMode(mode) {
    this._currentMode = mode;

    const activeStyle = [
      'background:var(--color-background-primary, #ffffff)',
      'border-color:var(--color-border-secondary, rgba(0,0,0,0.3))',
      'color:var(--color-text-primary, #1a1a18)',
    ].join(';');

    const inactiveStyle = [
      'background:transparent',
      'border-color:transparent',
      'color:var(--color-text-secondary, #5f5e5a)',
    ].join(';');

    // Active button styles
    this._voiceBtn.style.cssText = this._voiceBtn.style.cssText
      .replace(/background:[^;]+;?/g, '')
      .replace(/border-color:[^;]+;?/g, '')
      .replace(/color:[^;]+;?/g, '');

    [this._voiceBtn, this._panelBtn].forEach((btn) => {
      const isActive = btn.dataset.mode === mode;
      btn.setAttribute('aria-pressed', String(isActive));

      if (isActive) {
        btn.style.background = 'var(--color-background-primary, #ffffff)';
        btn.style.borderColor = 'var(--color-border-secondary, rgba(0,0,0,0.3))';
        btn.style.color = 'var(--color-text-primary, #1a1a18)';
      } else {
        btn.style.background = 'transparent';
        btn.style.borderColor = 'transparent';
        btn.style.color = 'var(--color-text-secondary, #5f5e5a)';
      }

      btn.disabled = false;
    });

    // Status dot colour
    const dotColors = {
      voice: '#1D9E75',   // teal-400 — voice active
      panel: '#7F77DD',   // purple-400 — panel active
      none:  'var(--color-border-secondary, rgba(0,0,0,0.3))',
    };
    this._statusDot.style.background = dotColors[mode] || dotColors.none;
    this._statusDot.title = `Mode: ${mode}`;

    this._pending = false;
  }

  _setPending() {
    this._pending = true;
    this._voiceBtn.disabled = true;
    this._panelBtn.disabled = true;
    this._statusDot.style.background = 'var(--color-border-secondary, rgba(0,0,0,0.3))';
  }

  // ── Event handlers ─────────────────────────────────────────────────────────

  async _onButtonClick(mode) {
    if (this._pending || mode === this._currentMode) return;

    this._setPending();

    try {
      const result = await _requestModeChange(mode);
      // If the API confirms the change synchronously, apply it.
      // The WebSocket event will also arrive and call _applyMode() again —
      // that is harmless (idempotent).
      if (result.changed) {
        this._applyMode(result.mode);
      } else {
        // Already in that mode — just restore visual state.
        this._applyMode(this._currentMode);
      }
    } catch (err) {
      console.error('ModeSwitcher: mode change failed —', err);
      // Restore the previous state so the button doesn't stay disabled.
      this._applyMode(this._currentMode);
      this._showError(err.message);
    }
  }

  _showError(message) {
    const toast = document.createElement('div');
    toast.textContent = `Mode switch failed: ${message}`;
    toast.style.cssText = [
      'position:absolute',
      'bottom:8px',
      'left:50%',
      'transform:translateX(-50%)',
      'background:var(--color-background-danger, #fcebeb)',
      'color:var(--color-text-danger, #a32d2d)',
      'border:0.5px solid var(--color-border-danger, #f09595)',
      'border-radius:6px',
      'padding:6px 14px',
      'font-size:12px',
      'pointer-events:none',
      'z-index:9999',
      'white-space:nowrap',
    ].join(';');

    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3500);
  }

  // ── Initial load ───────────────────────────────────────────────────────────

  async _loadInitialState() {
    try {
      const mode = await _fetchCurrentMode();
      this._applyMode(mode);
    } catch (err) {
      console.warn('ModeSwitcher: could not fetch initial mode —', err);
      this._applyMode('none');
    }
  }
}