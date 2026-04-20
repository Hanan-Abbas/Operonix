#!/bin/bash
echo "🚀 Initializing Operonix Environment..."

# 1. Install System Dependencies (Linux)
sudo apt update && sudo apt install -y libxcb-cursor0 portaudio19-dev

# 2. Install Python requirements
# We install packaging first to avoid the DeepFilterNet conflict
pip install "packaging>=23.0,<24.0"
pip install -r requirements.txt

echo "✅ Setup Complete. Run with: python3 -m core.main"