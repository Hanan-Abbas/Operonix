# =============================================================================
# Operonix — setup.sh
# =============================================================================

set -euo pipefail

# ── 1. Colour helpers ─────────────────────────────────────────────────────────

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓ $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $*${NC}"; }
err()  { echo -e "${RED}  ✗ $*${NC}"; }
info() { echo -e "${CYAN}  → $*${NC}"; }
hdr()  { echo -e "\n${BOLD}$*${NC}"; }

ERRORS=0
WARNINGS=0

# ── 2. System package installation ───────────────────────────────────────────

hdr "━━━ 2. System packages ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ "$EUID" -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
fi

info "Updating apt package index…"
$SUDO apt-get update -qq

SYSTEM_PACKAGES=(
    # ── Hybrid execution layer (REQUIRED for Bridge + focus-stack) ──────────
    wmctrl          # Z-order terminal list  →  TerminalResolver._list_terminals()
    xdotool         # Active window ID       →  TerminalResolver._get_active_window_id()
    xprop           # WM_CLASS lookup        →  TerminalResolver._get_wm_class()
    # ── Python runtime ───────────────────────────────────────────────────────
    python3-venv
    python3-pip
    python3-dev
    # ── Build dependencies for native wheels ─────────────────────────────────
    build-essential
    libffi-dev
    libxcb-cursor0
    libssl-dev
    libportaudio2       # voice/audio
    portaudio19-dev
    # ── Optional but recommended ──────────────────────────────────────────────
    gnome-terminal      # Profile C (Lab) — preferred terminal emulator
    curl
    git
)

info "Installing system packages: ${SYSTEM_PACKAGES[*]}"
$SUDO apt-get install -y "${SYSTEM_PACKAGES[@]}" \
    2>&1 | grep -E "(Installing|already installed|Unpacking|Setting up|ERROR)" \
    || true

ok "System packages installed."

# ── 3. ptrace relaxation ──────────────────────────────────────────────────────

hdr "━━━ 3. ptrace relaxation (Bridge profile) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

PTRACE_SCOPE_FILE="/proc/sys/kernel/yama/ptrace_scope"
SYSCTL_CONF="/etc/sysctl.d/99-operonix-ptrace.conf"

if [[ -f "$PTRACE_SCOPE_FILE" ]]; then
    CURRENT_SCOPE=$(cat "$PTRACE_SCOPE_FILE")
    info "Current ptrace_scope = ${CURRENT_SCOPE}"

    if [[ "$CURRENT_SCOPE" -eq 0 ]]; then
        ok "ptrace_scope already set to 0 — Bridge profile ready."
    else
        info "Relaxing ptrace_scope to 0 (current = ${CURRENT_SCOPE})…"
        echo 0 | $SUDO tee "$PTRACE_SCOPE_FILE" > /dev/null
        ok "ptrace_scope set to 0 for this session."

        # Persist across reboots via sysctl.d
        info "Writing persistent sysctl config: ${SYSCTL_CONF}"
        echo "kernel.yama.ptrace_scope = 0" | $SUDO tee "$SYSCTL_CONF" > /dev/null
        $SUDO sysctl --system -q 2>/dev/null || true
        ok "ptrace_scope = 0 persisted to ${SYSCTL_CONF}."
    fi
else
    warn "ptrace_scope file not found (non-Linux or Yama not loaded)."
    warn "Bridge profile will self-heal to Ghost if pts write fails."
    WARNINGS=$((WARNINGS + 1))
fi

# ── 4. Python virtual environment ─────────────────────────────────────────────

hdr "━━━ 4. Python virtual environment ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

VENV_DIR="$(dirname "$(realpath "$0")")/operonix"

if [[ -d "$VENV_DIR" ]]; then
    info "Virtual environment already exists at ${VENV_DIR}."
else
    info "Creating virtual environment at ${VENV_DIR}…"
    python3 -m venv "$VENV_DIR"
    ok "Virtual environment created."
fi

# Activate for the remainder of this script
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
ok "Virtual environment activated."

# ── 5. Python package installation ────────────────────────────────────────────

hdr "━━━ 5. Python packages ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

REQUIREMENTS_FILE="$(dirname "$(realpath "$0")")/requirements.txt"

pip install "packaging>=23.0,<24.0" --quiet
pip install --upgrade pip --quiet

if [[ -f "$REQUIREMENTS_FILE" ]]; then
    info "Installing from ${REQUIREMENTS_FILE}…"
    pip install -r "$REQUIREMENTS_FILE" --quiet
    ok "Python packages installed."
else
    warn "requirements.txt not found — skipping pip install."
    WARNINGS=$((WARNINGS + 1))
fi

# ── 6. Runtime verification checks — Dependency Sentry ───────────────────────


hdr "━━━ 6. Runtime verification (Dependency Sentry) ━━━━━━━━━━━━━━━━━━━━━━━━━"

check_binary() {
    local bin="$1"
    local role="$2"
    local required="${3:-true}"

    if command -v "$bin" &>/dev/null; then
        ok "${bin} found at $(command -v "$bin")  [${role}]"
        return 0
    else
        if [[ "$required" == "true" ]]; then
            err "${bin} NOT FOUND  [${role}] — install: sudo apt install ${bin}"
            ERRORS=$((ERRORS + 1))
        else
            warn "${bin} not found  [${role}] — optional; some features degraded"
            WARNINGS=$((WARNINGS + 1))
        fi
        return 1
    fi
}

# Core hybrid execution binaries
check_binary wmctrl  "Z-order terminal list (Bridge/Ghost routing)"         true
check_binary xdotool "Active window focus-stack polling (Bridge routing)"   true
check_binary xprop   "WM_CLASS terminal detection"                          true

# Terminal emulators for Profile C (Lab)
check_binary gnome-terminal "Profile C — Lab terminal spawn"    false
check_binary xterm          "Profile C — Lab fallback terminal" false

# Verify ptrace_scope value
if [[ -f "$PTRACE_SCOPE_FILE" ]]; then
    SCOPE_VAL=$(cat "$PTRACE_SCOPE_FILE")
    if [[ "$SCOPE_VAL" -eq 0 ]]; then
        ok "ptrace_scope = 0  [Bridge pts injection enabled]"
    else
        warn "ptrace_scope = ${SCOPE_VAL}  [Bridge profile will self-heal to Ghost]"
        WARNINGS=$((WARNINGS + 1))
    fi
fi

# Verify wmctrl actually returns windows (X11 session required)
info "Verifying wmctrl -l returns window list…"
if wmctrl -l &>/dev/null 2>&1; then
    WIN_COUNT=$(wmctrl -l 2>/dev/null | wc -l)
    ok "wmctrl -l  →  ${WIN_COUNT} window(s) detected."
else
    warn "wmctrl -l returned no output. Are you running under an X11 session?"
    warn "Wayland sessions require XWayland for wmctrl to work."
    WARNINGS=$((WARNINGS + 1))
fi

# Verify xdotool getactivewindow returns a non-zero ID
info "Verifying xdotool getactivewindow…"
if ACTIVE_WIN=$(xdotool getactivewindow 2>/dev/null); then
    if [[ -n "$ACTIVE_WIN" && "$ACTIVE_WIN" -ne 0 ]]; then
        ok "xdotool getactivewindow  →  window ID ${ACTIVE_WIN}"
    else
        warn "xdotool returned 0 — no active X11 window detected."
        WARNINGS=$((WARNINGS + 1))
    fi
else
    warn "xdotool getactivewindow failed. X11/XWayland may not be running."
    WARNINGS=$((WARNINGS + 1))
fi

# Verify Python environment
info "Verifying Python environment…"
if python3 -c "import asyncio, shutil, os; print('Python OK')" &>/dev/null; then
    PY_VER=$(python3 --version)
    ok "Python environment: ${PY_VER}"
else
    err "Python environment check failed."
    ERRORS=$((ERRORS + 1))
fi

# ── 7. Summary ────────────────────────────────────────────────────────────────

hdr "━━━ 7. Setup summary ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
if [[ "$ERRORS" -eq 0 && "$WARNINGS" -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}  All checks passed. Operonix is ready.${NC}"
elif [[ "$ERRORS" -eq 0 ]]; then
    echo -e "${YELLOW}${BOLD}  Setup complete with ${WARNINGS} warning(s).${NC}"
    echo -e "${YELLOW}  Bridge profile may degrade to Ghost in some scenarios.${NC}"
else
    echo -e "${RED}${BOLD}  Setup completed with ${ERRORS} error(s) and ${WARNINGS} warning(s).${NC}"
    echo -e "${RED}  Fix errors above before running Operonix.${NC}"
fi

echo ""
echo -e "  To start Operonix:"
echo -e "  ${CYAN}source operonix/bin/activate && python core/main.py${NC}"
echo ""

exit $ERRORS