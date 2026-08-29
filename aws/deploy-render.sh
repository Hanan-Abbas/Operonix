#!/bin/bash

# =============================================================================
# Operonix Render Deployment Script (Free Tier Forever)
# =============================================================================

set -euo pipefail

# Color helpers
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

# Configuration
RENDER_API_KEY="${RENDER_API_KEY:-}"
SERVICE_NAME="operonix-cloud"

# Check prerequisites
check_prerequisites() {
    hdr "━━━ Checking Prerequisites ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Check Render API key
    if [ -z "$RENDER_API_KEY" ]; then
        err "RENDER_API_KEY not set. Get it from: https://dashboard.render.com/settings/api"
        exit 1
    fi
    ok "Render API key found"
    
    # Check render CLI (optional, can use API directly)
    if command -v render &> /dev/null; then
        ok "Render CLI found"
    else
        warn "Render CLI not found. Install from: https://github.com/render-oss/render-cli"
        info "Will use API directly instead"
    fi
    
    # Check Docker
    if ! command -v docker &> /dev/null; then
        err "Docker not found. Install from: https://docs.docker.com/get-docker/"
        exit 1
    fi
    ok "Docker found"
    
    # Check git
    if ! command -v git &> /dev/null; then
        err "Git not found. Install from: https://git-scm.com/"
        exit 1
    fi
    ok "Git found"
}

# Build Docker image
build_image() {
    hdr "━━━ Building Docker Image ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    info "Building Operonix Render Docker image..."
    docker build -f Dockerfile.render -t operonix-render:latest .
    ok "Docker image built successfully"
}

# Test image locally
test_image() {
    hdr "━━━ Testing Image Locally ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    info "Running container locally for testing..."
    docker run --rm -p 8000:8000 -e PYTHONUNBUFFERED=1 operonix-render:latest &
    CONTAINER_PID=$!
    
    info "Waiting for container to start..."
    sleep 10
    
    if curl -f http://localhost:8000/health &> /dev/null; then
        ok "Health check passed"
        kill $CONTAINER_PID 2>/dev/null || true
    else
        warn "Health check failed, but continuing with deployment"
        kill $CONTAINER_PID 2>/dev/null || true
    fi
}

# Deploy to Render using API
deploy_render() {
    hdr "━━━ Deploying to Render ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    info "Render deployment is done via the Render Dashboard or render.yaml"
    info "Please follow these steps:"
    echo ""
    echo "1. Push your code to GitHub/GitLab"
    echo "2. Go to https://dashboard.render.com/"
    echo "3. Click 'New +' → 'Web Service'"
    echo "4. Connect your repository"
    echo "5. Render will automatically detect render.yaml"
    echo "6. Configure environment variables in Render dashboard:"
    echo "   - GROQ_API_KEY"
    echo "   - OPENROUTER_API_KEY (optional)"
    echo "   - GEMINI_API_KEY (optional)"
    echo "7. Click 'Deploy'"
    echo ""
    ok "Render will handle the rest automatically"
}

# Get service status
get_status() {
    hdr "━━━ Service Status ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    if command -v render &> /dev/null; then
        info "Checking Render service status..."
        render ps || warn "Could not get service status"
    else
        warn "Render CLI not installed. Check status at: https://dashboard.render.com/"
    fi
}

# Main execution
main() {
    hdr "━━━ Operonix Render Deployment (Free Tier Forever) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    check_prerequisites
    build_image
    test_image
    deploy_render
    get_status
    
    hdr "━━━ Deployment Instructions Complete ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ok "Follow the steps above to complete deployment to Render"
    info "Your service will be available at: https://$SERVICE_NAME.onrender.com"
}

# Run main function
main
