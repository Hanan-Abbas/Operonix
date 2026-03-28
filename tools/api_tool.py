import aiohttp
import asyncio
from core.event_bus import bus

class APITool:
    def __init__(self):
        self.name = "api_tool"

    async def run(self, action, args):
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
        else:
            text = await response.text()
            return False, f"HTTP {response.status}: {text}"

api_tool = APITool()