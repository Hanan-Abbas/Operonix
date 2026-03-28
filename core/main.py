class IOSAgent:
    def __init__(self):
        self.is_running = True

    async def initialize_modules(self):
        print("🚀 Operonix Agent: Starting engine...")
        
        # Order matters! Logger first to catch errors.
        await logger.start()
        await capability_mapper.start()
        await executor.start()
        await window_detector.start()
        await state_extractor.start()
        await llm_client.start()
        await planner.start()
        await orchestrator.start()

        print("✨ All modules are synchronized and listening to the Event Bus.")

    async def run_forever(self):
        """Keep the agent alive and start the dashboard server."""
        try:
            await self.initialize_modules()

            print("🌐 Dashboard API: Launching on http://localhost:8000")
            
            # Start the server. Since start_server is likely blocking, 
            # we run it in a way that doesn't stop our Event Bus.
            loop = asyncio.get_event_loop()
            
            # This launches the FastAPI server
            await loop.run_in_executor(None, start_server)

            # Keep the main thread alive so background tasks (Window Detector) continue
            while self.is_running:
                await asyncio.sleep(1)

        except Exception as e:
            print(f"💥 Critical System Failure: {e}")
        finally:
            self.cleanup()

    def cleanup(self):
        print("🔌 Powering down modules safely.")
        sys.exit(0)

# --- THIS IS THE PART YOU WERE MISSING ---
if __name__ == "__main__":
    agent = IOSAgent()
    try:
        # This tells Python to actually start the async loop
        asyncio.run(agent.run_forever())
    except KeyboardInterrupt:
        print("\n👋 Operonix Agent: Offline. Goodbye.")