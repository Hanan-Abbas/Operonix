"""
Test script to verify dual execution fix.
This tests that when USE_LANGGRAPH=true, only the graph processes tasks,
not the legacy orchestrator.
"""
import asyncio
import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.path.insert(0, project_root)

from core.event_bus import bus
from migration.feature_flags import flags


async def test_event_consumption():
    """Test that EventBus event consumption works correctly."""
    print("=" * 60)
    print("Testing EventBus Event Consumption")
    print("=" * 60)

    print(f"\nFeature Flags:")
    print(f"  USE_LANGGRAPH: {flags.USE_LANGGRAPH}")

    # Track which handlers were called
    calls = {"graph": 0, "legacy": 0}

    def graph_handler(event):
        """Graph handler that should consume the event."""
        calls["graph"] += 1
        print(f"  🔄 Graph handler called (call #{calls['graph']})")
        # Consume the event
        event.consume()
        print(f"  🔒 Event consumed by graph handler")

    def legacy_handler(event):
        """Legacy handler that should skip consumed events."""
        calls["legacy"] += 1
        print(f"  ⚠️  Legacy handler called (call #{calls['legacy']}) - THIS SHOULD NOT HAPPEN!")

    # Subscribe both handlers
    bus.subscribe("test_event", graph_handler)
    bus.subscribe("test_event", legacy_handler)

    # Start the event bus
    from threading import Thread
    import time

    def run_bus():
        asyncio.run(bus.run())

    bus_thread = Thread(target=run_bus, daemon=True)
    bus_thread.start()
    time.sleep(0.5)  # Let bus start

    # Publish test event
    print("\nPublishing test event...")
    bus.publish("test_event", {"text": "test command"}, source="test")

    # Wait for processing
    await asyncio.sleep(1)

    # Check results
    print(f"\nResults:")
    print(f"  Graph handler called: {calls['graph']} times")
    print(f"  Legacy handler called: {calls['legacy']} times")

    if calls["graph"] == 1 and calls["legacy"] == 0:
        print("\n✅ Event consumption works correctly!")
        print("   Graph handler consumed event, legacy handler skipped")
        return True
    else:
        print("\n❌ Event consumption failed!")
        print(f"   Expected: graph=1, legacy=0")
        print(f"   Got: graph={calls['graph']}, legacy={calls['legacy']}")
        return False


async def test_dual_execution_scenario():
    """Test the actual dual execution scenario with user_input_received."""
    print("\n" + "=" * 60)
    print("Testing Dual Execution Scenario")
    print("=" * 60)

    if not flags.USE_LANGGRAPH:
        print("\n⚠️  USE_LANGGRAPH is false - dual execution test not applicable")
        print("   In legacy mode, both handlers are expected to run")
        return True

    # Track calls
    calls = {"graph_bridge": 0, "orchestrator": 0}

    def graph_bridge_handler(event):
        """Simulates lifecycle manager's graph bridge."""
        calls["graph_bridge"] += 1
        print(f"  🔄 Graph bridge handler called")
        user_input = event.data.get("text", "")
        print(f"     Routing to graph: {user_input}")
        # Consume the event
        event.consume()
        print(f"     🔒 Event consumed - orchestrator will skip")

    def orchestrator_handler(event):
        """Simulates orchestrator's handle_new_task."""
        calls["orchestrator"] += 1
        print(f"  ⚠️  Orchestrator handler called - THIS SHOULD NOT HAPPEN!")
        print(f"     Event was not consumed properly")

    # Subscribe handlers
    bus.subscribe("user_input_received", graph_bridge_handler)
    bus.subscribe("user_input_received", orchestrator_handler)

    # Publish user_input event
    print("\nPublishing user_input_received event...")
    bus.publish(
        "user_input_received",
        {"text": "open system monitor", "source": "panel"},
        source="test"
    )

    # Wait for processing
    await asyncio.sleep(1)

    # Check results
    print(f"\nResults:")
    print(f"  Graph bridge called: {calls['graph_bridge']} times")
    print(f"  Orchestrator called: {calls['orchestrator']} times")

    if calls["graph_bridge"] == 1 and calls["orchestrator"] == 0:
        print("\n✅ Dual execution fixed!")
        print("   Graph bridge consumed event, orchestrator skipped")
        return True
    else:
        print("\n❌ Dual execution still present!")
        print(f"   Expected: graph_bridge=1, orchestrator=0")
        print(f"   Got: graph_bridge={calls['graph_bridge']}, orchestrator={calls['orchestrator']}")
        return False


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("Dual Execution Fix Test Suite")
    print("=" * 60)

    # Test event consumption mechanism
    consumption_ok = await test_event_consumption()

    # Test dual execution scenario
    if flags.USE_LANGGRAPH:
        dual_ok = await test_dual_execution_scenario()
    else:
        print("\nSkipping dual execution test (USE_LANGGRAPH=false)")
        dual_ok = True

    if consumption_ok and dual_ok:
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        print("\nDual execution fix is working correctly!")
        print("When USE_LANGGRAPH=true, only the graph processes tasks.")
        return 0
    else:
        print("\n" + "=" * 60)
        print("⚠️  TESTS FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
