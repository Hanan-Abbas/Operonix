# Operonix Local Agent Setup Guide

## Quick Start for Friends

This guide helps you set up the Operonix local agent to connect to a shared cloud backend.

## What You Need

- **Linux computer** (currently Linux-only)
- **Python 3.11+** installed
- **Internet connection**
- **Two files from your friend**:
  - `local_agent.py` (the agent script)
  - `local_agent_requirements.txt` (dependencies list)
- **Cloud backend URL** (e.g., `https://operonix-cloud.onrender.com`)

## Installation Steps

### Step 1: Install System Dependencies

Open your terminal and run:

```bash
sudo apt-get update
sudo apt-get install -y \
    wmctrl \
    xdotool \
    x11-utils \
    python3-dev \
    python3-pip
```

### Step 2: Create a Working Directory

```bash
mkdir operonix-agent
cd operonix-agent
```

### Step 3: Copy the Files

Copy these files from your friend into the `operonix-agent` directory:
- `local_agent.py`
- `local_agent_requirements.txt`

### Step 4: Install Python Dependencies

```bash
pip install -r local_agent_requirements.txt
```

### Step 5: Choose Your Session ID

Pick a unique identifier for your computer. This can be:
- Your name: `alex-laptop`
- Your computer name: `desktop-pc-1`
- Any unique string: `user-12345`

### Step 6: Run the Agent

Replace `YOUR_SESSION_ID` and `BACKEND_URL` with your actual values:

```bash
python local_agent.py \
    --session-id YOUR_SESSION_ID \
    --backend-url BACKEND_URL
```

Example:
```bash
python local_agent.py \
    --session-id alex-laptop \
    --backend-url https://operonix-cloud.onrender.com
```

### Step 7: Connect to Dashboard

1. The agent will display your session ID when it starts
2. Open the cloud backend URL in your browser
3. Enter your session ID in the connection field
4. You're now connected!

## What Happens Next

- The agent will automatically try to connect to the cloud backend
- If the cloud backend is sleeping (Render free tier), it will wait and retry every 5 seconds
- Once connected, you can control your computer through the web dashboard
- The agent will automatically reconnect if the connection drops

## Session ID Examples

Choose a unique session ID that identifies your computer:

```bash
# Good examples
python local_agent.py --session-id alex-laptop --backend-url https://...
python local_agent.py --session-id sarah-desktop --backend-url https://...
python local_agent.py --session-id work-computer-1 --backend-url https://...

# Avoid common names
python local_agent.py --session-id laptop --backend-url https...  # Too common
python local_agent.py --session-id agent --backend-url https...   # Too common
```

## Troubleshooting

### Connection Issues

**"Waiting for cloud backend to wake up..."**
- This is normal! Render free tier services sleep when inactive
- The agent will automatically retry every 5 seconds
- Wait up to 5 minutes for the backend to wake up

**"Failed to connect after 60 attempts"**
- Check your internet connection
- Verify the backend URL is correct
- Ask your friend if the cloud backend is deployed

### Permission Issues

**"Permission denied" errors**
- Make sure you have permissions for desktop automation
- Try running without sudo first (recommended)
- Some systems may require X11 permissions

### Dependencies Issues

**"Module not found" errors**
- Make sure you installed the requirements: `pip install -r local_agent_requirements.txt`
- Check Python version: `python3 --version` (should be 3.11+)

### Wayland vs X11

**Desktop automation not working**
- Operonix currently requires X11 (not Wayland)
- Check if you're running X11: `echo $XDG_SESSION_TYPE`
- If Wayland, you may need to switch to X11 or use XWayland

## Running in Background

To run the agent in the background:

```bash
# Using nohup
nohup python local_agent.py \
    --session-id YOUR_SESSION_ID \
    --backend-url BACKEND_URL > agent.log 2>&1 &

# Check logs
tail -f agent.log
```

## Auto-start on Boot

To start the agent automatically when you log in:

### Using systemd (recommended)

1. Create a service file:
```bash
sudo nano /etc/systemd/system/operonix-agent.service
```

2. Add this content (replace paths and IDs):
```ini
[Unit]
Description=Operonix Local Agent
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/operonix-agent
ExecStart=/usr/bin/python3 /home/YOUR_USERNAME/operonix-agent/local_agent.py \
    --session-id YOUR_SESSION_ID \
    --backend-url BACKEND_URL
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

3. Enable and start the service:
```bash
sudo systemctl enable operonix-agent
sudo systemctl start operonix-agent
sudo systemctl status operonix-agent
```

## Security Notes

- **Session IDs are like passwords** - don't share them publicly
- Only connect to trusted backend URLs
- The agent requires desktop automation permissions
- Your friend can see your session ID in the dashboard

## What You Can Control

Once connected, the cloud backend can:
- Take screenshots of your screen
- Click at specific coordinates
- Type text
- Press keyboard shortcuts
- Get list of open windows
- Focus specific windows

**You remain in control** - you can stop the agent anytime by pressing Ctrl+C.

## Getting Help

If you have issues:
1. Check the agent logs for error messages
2. Verify your session ID is unique
3. Ensure the cloud backend URL is correct
4. Ask your friend for help with backend issues

## Summary

1. Install system dependencies
2. Copy `local_agent.py` and `local_agent_requirements.txt`
3. Install Python dependencies
4. Run with your unique session ID
5. Connect via web dashboard
6. Enjoy automated desktop control!

**Total setup time: ~5-10 minutes**
**Cost: $0.00** (uses your existing computer)
