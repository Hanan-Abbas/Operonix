#!/usr/bin/env python3
"""
Operonix Local Agent
Lightweight desktop automation worker that connects to cloud backend

This script runs on the user's local machine and handles:
- Screen reading and UI automation
- Voice input processing
- Local command execution
- Communication with cloud backend via WebSocket

Usage:
    python local_agent.py --session-id <SESSION_ID> --backend-url <BACKEND_URL>
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from typing import Optional
import argparse

import websockets
from pyautogui import screenshot, click, write, press, hotkey
import xdotool
import wmctrl

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("LocalAgent")


class LocalAgent:
    """Lightweight local agent for desktop automation"""
    
    def __init__(self, session_id: str, backend_url: str):
        self.session_id = session_id
        self.backend_url = backend_url.replace("http://", "ws://").replace("https://", "wss://")
        if not self.backend_url.endswith("/ws/agent"):
            self.backend_url = f"{self.backend_url}/ws/agent"
        
        self.websocket = None
        self.running = False
        
    async def connect(self):
        """Connect to cloud backend via WebSocket"""
        try:
            logger.info(f"Connecting to backend: {self.backend_url}")
            self.websocket = await websockets.connect(
                self.backend_url,
                extra_headers={"X-Session-ID": self.session_id}
            )
            logger.info("Connected to cloud backend")
            self.running = True
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            raise
    
    async def handle_command(self, command: dict):
        """Execute automation commands from cloud backend"""
        try:
            cmd_type = command.get("type")
            logger.info(f"Executing command: {cmd_type}")
            
            if cmd_type == "screenshot":
                return await self.take_screenshot(command)
            elif cmd_type == "click":
                return await self.click_element(command)
            elif cmd_type == "type":
                return await self.type_text(command)
            elif cmd_type == "keypress":
                return await self.press_key(command)
            elif cmd_type == "hotkey":
                return await self.hotkey_combo(command)
            elif cmd_type == "get_windows":
                return await self.get_windows()
            elif cmd_type == "focus_window":
                return await self.focus_window(command)
            else:
                return {"status": "error", "message": f"Unknown command: {cmd_type}"}
                
        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return {"status": "error", "message": str(e)}
    
    async def take_screenshot(self, command: dict) -> dict:
        """Capture screenshot and return base64 encoded image"""
        try:
            screenshot_path = command.get("path", "/tmp/screenshot.png")
            screenshot(screenshot_path)
            
            # Read and encode
            with open(screenshot_path, "rb") as f:
                import base64
                image_data = base64.b64encode(f.read()).decode()
            
            return {
                "status": "success",
                "image_data": image_data,
                "path": screenshot_path
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    async def click_element(self, command: dict) -> dict:
        """Click at specified coordinates"""
        try:
            x = command.get("x")
            y = command.get("y")
            if x is not None and y is not None:
                click(x, y)
                return {"status": "success", "x": x, "y": y}
            else:
                return {"status": "error", "message": "Missing x or y coordinates"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    async def type_text(self, command: dict) -> dict:
        """Type text at current cursor position"""
        try:
            text = command.get("text", "")
            interval = command.get("interval", 0.0)
            write(text, interval=interval)
            return {"status": "success", "text": text}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    async def press_key(self, command: dict) -> dict:
        """Press a single key"""
        try:
            key = command.get("key")
            press(key)
            return {"status": "success", "key": key}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    async def hotkey_combo(self, command: dict) -> dict:
        """Press hotkey combination"""
        try:
            keys = command.get("keys", [])
            if len(keys) >= 2:
                hotkey(*keys)
                return {"status": "success", "keys": keys}
            else:
                return {"status": "error", "message": "Hotkey requires at least 2 keys"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    async def get_windows(self) -> dict:
        """Get list of open windows"""
        try:
            windows = wmctrl.get_window_list()
            return {
                "status": "success",
                "windows": [
                    {
                        "id": win.get("id"),
                        "title": win.get("title"),
                        "class": win.get("class")
                    }
                    for win in windows
                ]
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    async def focus_window(self, command: dict) -> dict:
        """Focus a specific window"""
        try:
            window_id = command.get("window_id")
            if window_id:
                xdotool.window_focus(window_id)
                return {"status": "success", "window_id": window_id}
            else:
                return {"status": "error", "message": "Missing window_id"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    async def listen(self):
        """Listen for commands from cloud backend"""
        try:
            while self.running:
                message = await self.websocket.recv()
                command = json.loads(message)
                
                logger.info(f"Received command: {command.get('type')}")
                result = await self.handle_command(command)
                
                # Send result back
                await self.websocket.send(json.dumps(result))
                
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Connection to backend closed")
            self.running = False
        except Exception as e:
            logger.error(f"Error in listen loop: {e}")
            self.running = False
    
    async def run(self):
        """Main agent loop"""
        await self.connect()
        
        # Send initial connection message
        await self.websocket.send(json.dumps({
            "type": "connect",
            "session_id": self.session_id,
            "capabilities": [
                "screenshot",
                "click", 
                "type",
                "keypress",
                "hotkey",
                "get_windows",
                "focus_window"
            ]
        }))
        
        # Start listening for commands
        await self.listen()
    
    async def stop(self):
        """Stop the agent"""
        self.running = False
        if self.websocket:
            await self.websocket.close()
        logger.info("Agent stopped")


def generate_session_id() -> str:
    """Generate a unique session ID"""
    return f"agent-{uuid.uuid4().hex[:8]}"


async def main():
    parser = argparse.ArgumentParser(description="Operonix Local Agent")
    parser.add_argument(
        "--session-id",
        type=str,
        help="Session ID (auto-generated if not provided)"
    )
    parser.add_argument(
        "--backend-url",
        type=str,
        default="https://operonix-cloud.onrender.com",
        help="Cloud backend URL"
    )
    
    args = parser.parse_args()
    
    # Generate session ID if not provided
    session_id = args.session_id or generate_session_id()
    
    logger.info(f"Starting Operonix Local Agent")
    logger.info(f"Session ID: {session_id}")
    logger.info(f"Backend URL: {args.backend_url}")
    logger.info(f"Enter this session ID in the cloud dashboard to connect")
    
    agent = LocalAgent(session_id, args.backend_url)
    
    try:
        await agent.run()
    except KeyboardInterrupt:
        logger.info("Shutting down agent...")
        await agent.stop()
    except Exception as e:
        logger.error(f"Agent error: {e}")
        await agent.stop()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
