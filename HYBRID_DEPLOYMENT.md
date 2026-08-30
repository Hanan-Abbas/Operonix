# Operonix Hybrid Deployment Guide

## Overview

This guide covers deploying Operonix using a **hybrid architecture** that costs **$0.00 forever** by splitting the system into:

- **Cloud Backend (Zeabur)**: LLM orchestrator, API endpoints, dashboard - free forever
- **Local Agent (Your Laptop)**: Desktop automation, screen reading, voice - runs locally

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                  Cloud Backend (Zeabur)                         │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  • FastAPI Server & Dashboard                            │   │
│  │  • LLM Orchestrator (Groq/Gemini/OpenRouter)             │   │
│  │  • Memory & Learning Systems                             │   │
│  │  • Plugin Management                                     │   │
│  │  • Session Management                                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                          ↕ WebSocket                            │
└─────────────────────────────────────────────────────────────────┘
                          ↕ WebSocket
┌─────────────────────────────────────────────────────────────────┐
│                  Local Agent (Your Laptop)                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  • Screen Reading & UI Automation                        │   │
│  │  • Voice Input Processing                                 │   │
│  │  • Local Command Execution                               │   │
│  │  • Desktop Application Control                           │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Why This Architecture?

### Benefits
- **$0.00 Forever**: Zeabur free tier has no expiration date
- **Scales to Zero**: Cloud backend sleeps when not in use
- **Multi-User Support**: Multiple users can share the same cloud backend
- **Local Performance**: Your laptop handles heavy desktop operations
- **Privacy**: Sensitive data stays on your local machine
- **Better Builds**: 4GB RAM burst during builds for faster deployment

### Trade-offs
- Each user must run the local agent script
- Requires internet connection for cloud communication
- Desktop automation only works when local agent is running

## Prerequisites

### For Cloud Backend (Zeabur)
- GitHub account
- Zeabur account (free, no credit card required)
- LLM API keys (Groq recommended for free tier)

### For Local Agent
- Linux machine (currently Linux-only)
- Python 3.11+
- Desktop automation dependencies (xdotool, wmctrl)
- Internet connection

## Deployment Steps

### Step 1: Deploy Cloud Backend to Zeabur

#### 1.1 Prepare Your Repository

```bash
# Ensure zeabur.yaml and Dockerfile.render are in your repository
git add zeabur.yaml Dockerfile.render
git commit -m "Add Zeabur deployment configuration"
git push origin main
```

#### 1.2 Deploy to Zeabur

1. Go to [Zeabur Dashboard](https://zeabur.com)
2. Sign up with GitHub (no credit card required)
3. Click **"Create New Project"**
4. Select your Operonix repository from GitHub
5. Zeabur will automatically detect `zeabur.yaml`
6. Configure environment variables:
   - `GROQ_API_KEY`: Your Groq API key (recommended, mark as secret)
   - `OPENROUTER_API_KEY`: Optional alternative (mark as secret)
   - `GEMINI_API_KEY`: Optional alternative (mark as secret)
   - `CORS_ORIGINS`: `*` (or your domain)
7. Click **"Deploy"**

#### 1.3 Get Your Service URL

After deployment, Zeabur will provide a URL like:
```
https://operonix-cloud.zeabur.app
```

Save this URL for the local agent configuration.

### Step 2: Set Up Local Agent

#### 2.1 Install Dependencies

```bash
# Install system dependencies
sudo apt-get update
sudo apt-get install -y \
    wmctrl \
    xdotool \
    x11-utils \
    python3-dev \
    python3-pip

# Install Python dependencies
pip install -r local_agent_requirements.txt
```

#### 2.2 Run the Local Agent

```bash
# Generate a session ID (or use your own)
SESSION_ID="agent-$(uuidgen | cut -d'-' -f1)"

# Run the agent
python local_agent.py \
    --session-id $SESSION_ID \
    --backend-url https://operonix-cloud.zeabur.app
```

The agent will display your session ID:
```
Session ID: agent-a1b2c3d4
Enter this session ID in the cloud dashboard to connect
```

**Automatic Reconnection**: The agent automatically handles Render's wake-up:
- If the cloud backend is sleeping, the agent will retry every 5 seconds
- It will wait up to 5 minutes for the backend to wake up
- If the connection drops, it will automatically reconnect
- You'll see "Waiting for cloud backend to wake up..." during wake-up

#### 2.3 Connect Agent to Cloud

1. Open your Render dashboard URL in a browser
2. Enter your session ID in the connection field
3. The cloud backend will now route automation commands to your local agent

### Step 3: Use the System

#### For Single User

1. Start your local agent with your session ID
2. Open the cloud dashboard
3. Enter your session ID
4. Use the dashboard to control your local machine

#### For Multiple Users

Each user needs to:

1. Run the local agent on their machine with a unique session ID
2. Open the shared cloud dashboard
3. Enter their unique session ID
4. The cloud backend routes commands to the correct local agent

## Configuration

### Cloud Backend Environment Variables

Configure these in Render dashboard:

```bash
# LLM Provider (choose one or more)
GROQ_API_KEY=gsk_...
OPENROUTER_API_KEY=sk-or-...
GEMINI_API_KEY=AI...

# Application Settings
LOG_LEVEL=INFO
CORS_ORIGINS=*
PYTHONUNBUFFERED=1
```

### Local Agent Configuration

```bash
# Session ID (auto-generated or custom)
--session-id agent-unique-id

# Backend URL
--backend-url https://your-app.onrender.com
```

## Multi-Tenancy & Security

### Session ID Mechanism

The system uses session IDs to ensure commands are routed to the correct local agent:

1. **Agent Registration**: Local agent connects with unique session ID
2. **Session Mapping**: Cloud backend maps session IDs to WebSocket connections
3. **Command Routing**: Cloud routes commands to the correct agent based on session ID
4. **Isolation**: Each user's commands are isolated to their machine

### Security Considerations

- **Session IDs**: Treat session IDs like passwords - don't share publicly
- **HTTPS**: Always use HTTPS for cloud backend
- **API Keys**: Store API keys in Render environment variables, not in code
- **CORS**: Configure CORS origins to your specific domains in production

## Troubleshooting

### Cloud Backend Issues

**Service won't start:**
- Check Render logs for errors
- Verify environment variables are set correctly
- Ensure Dockerfile.render is valid

**Service sleeps too quickly:**
- Render free tier sleeps after 15 minutes of inactivity
- This is expected behavior - the service will wake on next request

**API errors:**
- Check LLM API key is valid
- Verify CORS configuration
- Check Render service logs

### Local Agent Issues

**Connection failures:**
- Verify backend URL is correct
- Check internet connection
- Ensure cloud backend is running

**Automation commands fail:**
- Verify xdotool and wmctrl are installed
- Check you're running under X11 (not Wayland)
- Ensure you have permissions for desktop automation

**Session ID conflicts:**
- Each agent must have a unique session ID
- Regenerate session ID if conflicts occur

### WebSocket Issues

**Connection drops:**
- Check internet connection stability
- Verify Render service is not sleeping
- Check WebSocket timeout settings

**Commands not reaching agent:**
- Verify session ID is correctly registered
- Check agent is still running
- Review cloud backend logs for routing errors

## Advanced Configuration

### Custom Session Management

For production use, consider implementing:

1. **Authentication**: Add token-based authentication for session registration
2. **Session Expiration**: Implement session timeout for security
3. **Session Persistence**: Store active sessions in database
4. **Session Recovery**: Allow reconnection with same session ID

### Load Balancing

For high-availability:

1. Deploy multiple Render services
2. Use load balancer to distribute connections
3. Implement session affinity for WebSocket connections

### Monitoring

Add monitoring for:

- Cloud backend health and performance
- Local agent connection status
- Command success/failure rates
- LLM API usage and costs

## Cost Analysis

### Render Free Tier (Forever)

- **Web Service**: Free
- **CPU**: 512 MB RAM (free tier)
- **Bandwidth**: 100 GB/month (free)
- **Build Minutes**: 750/month (free)

### Local Agent

- **Cost**: $0 (runs on your existing hardware)
- **Resources**: Minimal CPU and memory usage

### Total Cost

**$0.00/month** (forever)

## Comparison with Other Deployments

| Feature | Hybrid (Render) | AWS App Runner | AWS Lambda |
|---------|----------------|---------------|------------|
| **Cost** | Free forever | $43/month after free tier | Free tier limited |
| **Desktop Automation** | ✅ Full support | ✅ Full support | ❌ Not supported |
| **Scales to Zero** | ✅ Yes | ❌ No | ✅ Yes |
| **Setup Complexity** | Medium | Medium | High |
| **Multi-User** | ✅ Easy | ✅ Possible | ❌ Complex |
| **Long-running Tasks** | ✅ Supported | ✅ Supported | ❌ 15-min limit |

## Migration from Full Docker

If you're currently using the full Docker deployment:

1. **Keep existing setup** for development/testing
2. **Add Render deployment** for production cloud backend
3. **Update local agent** to connect to Render instead of local Docker
4. **Gradually migrate users** to the hybrid architecture

## Support

For issues or questions:
- GitHub Issues: https://github.com/Hanan-Abbas/Operonix/issues
- Render Documentation: https://render.com/docs
- Local Agent Issues: Check logs and system dependencies

## Summary

The hybrid deployment provides:
- ✅ **$0.00 forever** - No ongoing costs
- ✅ **Full functionality** - Complete Operonix features
- ✅ **Multi-user support** - Share cloud backend
- ✅ **Scales to zero** - No idle costs
- ✅ **Local performance** - Desktop automation on your machine

Perfect for:
- Personal projects with no budget
- Small teams sharing resources
- Development and testing
- Demonstrations and prototypes
