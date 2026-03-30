# core/main.py
import asyncio
from core.lifecycle_manager import lifecycle_manager


async def main():
    await lifecycle_manager.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Operonix Agent: Offline. Goodbye.")