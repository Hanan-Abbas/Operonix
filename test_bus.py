# Save this as test_bus.py and run it
import asyncio
from core.event_bus import bus

async def test_trigger():
    print("📡 Sending manual trigger...")
    await bus.emit("user_input_received", {"text": "Create a file named status.txt"}, source="manual_test")

if __name__ == "__main__":
    asyncio.run(test_trigger())