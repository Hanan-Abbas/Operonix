# Operonix Dockerfile
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    # X11 and window management (for automation)
    wmctrl \
    xdotool \
    x11-utils \
    xprop \
    # Python build dependencies
    python3-dev \
    build-essential \
    libffi-dev \
    libxcb-cursor0 \
    libssl-dev \
    # Audio dependencies for voice
    libportaudio2 \
    portaudio19-dev \
    # Terminal emulators
    gnome-terminal \
    xterm \
    # Other utilities
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN useradd -m -u 1000 operonix && \
    mkdir -p /home/operonix/app && \
    chown -R operonix:operonix /home/operonix

WORKDIR /home/operonix/app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir "packaging>=23.0,<24.0" --force-reinstall && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=operonix:operonix . .

# Set ptrace_scope for Bridge profile
RUN echo 0 | tee /proc/sys/kernel/yama/ptrace_scope || true

# Create necessary directories
RUN mkdir -p logs plugins/installed memory data && \
    chown -R operonix:operonix logs plugins/installed memory data

# Switch to non-root user
USER operonix

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command - run the main application
CMD ["python3", "-m", "core.main"]
