#!/bin/bash

# =============================================================================
# Operonix Local Agent Setup Script
# Minimal setup for the local agent in hybrid deployment
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

hdr "━━━ Operonix Local Agent Setup ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ "$EUID" -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
fi

info "Updating apt package index…"
$SUDO apt-get update -qq

# Minimal system packages for local agent
SYSTEM_PACKAGES=(
    wmctrl          # Window management
    xdotool         # Desktop automation
    x11-utils       # X11 utilities
    python3-dev     # Python development
    python3-pip     # Python package manager
)

info "Installing system packages: ${SYSTEM_PACKAGES[*]}"
$SUDO apt-get install -y "${SYSTEM_PACKAGES[@]}" \
    2>&1 | grep -E "(Installing|already installed|Unpacking|Setting up|ERROR)" \
    || true

ok "System packages installed."

# ── 3. Python package installation ───────────────────────────────────────────

hdr "━━━ Python Dependencies ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

REQUIREMENTS_FILE="$(dirname "$(realpath "$0")")/local_agent_requirements.txt"

if [[ -f "$REQUIREMENTS_FILE" ]]; then
    info "Installing from ${REQUIREMENTS_FILE}…"
    pip3 install --upgrade pip --quiet
    pip3 install -r "$REQUIREMENTS_FILE" --quiet
    ok "Python packages installed."
else
    warn "local_agent_requirements.txt not found."
    warn "Installing minimal dependencies manually…"
    pip3 install --upgrade pip --quiet
    pip3 install websockets pyautogui python-xlib Pillow --quiet
    ok "Minimal Python packages installed."
    WARNINGS=$((WARNINGS + 1))
fi

# ── 4. Runtime verification ─────────────────────────────────────────────────────

hdr "━━━ Verification Checks ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

check_binary() {
    local bin="$1"
    local role="$2"
    local required="${3:-true}"

    if command -v "$bin" &>/dev/null; then
        ok "${bin} found  [${role}]"
        return 0
    else
        if [[ "$required" == "true" ]]; then
            err "${bin} NOT FOUND  [${role}] — install: sudo apt install ${bin}"
            ERRORS=$((ERRORS + 1))
        else
            warn "${bin} not found  [${role}] — optional"
            WARNINGS=$((WARNINGS + 1))
        fi
        return 1
    fi
}

# Check required binaries
check_binary wmctrl  "Window management" true
check_binary xdotool "Desktop automation" true
check_binary python3 "Python runtime" true

# Verify wmctrl works (X11 check)
info "Verifying X11 session (wmctrl -l)…"
if wmctrl -l &>/dev/null 2>&1; then
    WIN_COUNT=$(wmctrl -l 2>/dev/null | wc -l)
    ok "X11 session detected: ${WIN_COUNT} window(s)"
else
    warn "wmctrl -l failed. Are you running under X11?"
    warn "Wayland sessions require XWayland for desktop automation."
    WARNINGS=$((WARNINGS + 1))
fi

# Verify xdotool works
info "Verifying xdotool…"
if ACTIVE_WIN=$(xdotool getactivewindow 2>/dev/null); then
    if [[ -n "$ACTIVE_WIN" && "$ACTIVE_WIN" -ne 0 ]]; then
        ok "xdotool working: window ID ${ACTIVE_WIN}"
    else
        warn "xdotool returned 0 — no active window detected."
        WARNINGS=$((WARNINGS + 1))
    fi
else
    warn "xdotool failed. X11/XWayland may not be running."
    WARNINGS=$((WARNINGS + 1))
fi

# ── 5. Summary ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

hdr "━━━ Setup Summary ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
if [[ "$ERRORS" -eq 0 && "$WARNINGS" -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}  All checks passed. Local agent is ready!${NC}"
elif [[ "$ERRORS" -eq 0 ]]; then
    echo -e "${YELLOW}${BOLD}  Setup complete with ${WARNINGS} warning(s).${NC}"
    echo -e "${YELLOW}  Agent should still work, but some features may be degraded.${NC}"
else
    echo -e "${RED}${BOLD}  Setup completed with ${ERRORS} error(s) and ${WARNINGS} warning(s).${NC}"
    echo -e "${RED}  Fix errors above before running the agent.${NC}"
fi

echo ""
echo -e "  To run the local agent:"
echo -e "  ${CYAN}python local_agent.py --session-id YOUR_ID --backend-url YOUR_URL${NC}"
echo ""
echo -e "  Example:"
echo -e "  ${CYAN}python local_agent.py --session-id alex-laptop --backend-url https://operonix-cloud.onrender.com${NC}"
echo ""

exit $ERRORS
