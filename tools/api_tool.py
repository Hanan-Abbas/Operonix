"""
tools/api_tool.py
──────────────────
HTTP / API operations tool.

Changes from original
──────────────────────
• Added `supported_intents` set and `can_handle()`.
• Added `tool_type = "api_tool"` for priority resolution.
• Everything else is identical to the original.
"""
from __future__ import annotations

import aiohttp

from core.event_bus import bus


class APITool:
    name = "api_tool"
    tool_type = "api_tool"

    supported_intents: set[str] = {
        "extract_text", "fill_form", "submit_form", "click_link",
        "api_call", "webhook",
    }

    def can_handle(self, intent: str) -> bool:
        return intent in self.supported_intents

    async def run(self, action: str, args: dict):
        url = args.get("url")
        method = args.get("method", "GET").upper()
        payload = args.get("data", {})
        headers = args.get("headers", {})

        await bus.emit("api_op_started", {"url": url, "method": method}, source="api_tool")

        try:
            async with aiohttp.ClientSession() as session:
                if method == "GET":
                    async with session.get(url, headers=headers, timeout=10) as resp:
                        return await self._handle_response(resp)
                elif method == "POST":
                    async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
                        return await self._handle_response(resp)
            return False, f"Method {method} not supported."
        except Exception as e:
            return False, f"API Error: {str(e)}"

    async def _handle_response(self, response):
        if response.status < 300:
            data = await response.json()
            return True, data
        text = await response.text()
        return False, f"HTTP {response.status}: {text}"


api_tool = APITool()