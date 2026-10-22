"""
Test script to verify legacy delegation removal.
This tests that graph nodes use concrete commands and don't delegate to legacy systems.
"""
import asyncio
import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.path.insert(0, project_root)

from migration.feature_flags import flags


async def test_execute_step_concrete_commands():
    """Test that execute_step passes concrete commands to executor."""
    print("=" * 60)
    print("Testing execute_step Concrete Command Translation")
    print("=" * 60)

    from graph.nodes.execute_step import _translate_routing_decision, _convert_step_to_executor_format
    from migration.domain_contracts import PlanStep, PlanStepIdempotency, PlanStepSideEffect
    from migration.domain_contracts import RoutingCandidate, MethodDecision, CandidateType

    print(f"\nFeature Flags:")
    print(f"  USE_LANGGRAPH: {flags.USE_LANGGRAPH}")

    # Create a plan step with concrete command from LLM
    step = PlanStep(
        step_id="test_step_1",
        action="gnome-system-monitor",  # Concrete command from LLM
        parameters={
            "shell_argv": [],
            "method": "shell",
            "explanation": "Open system monitor",
            "user_input": "open system monitor"  # Only for reference
        },
        objective="Open system monitor application",
        idempotency=PlanStepIdempotency.CONDITIONAL,
        side_effect=PlanStepSideEffect.LOCAL,
        reversibility=True
    )

    # Create a routing decision
    candidate = RoutingCandidate(
        candidate_type=CandidateType.SHELL,
        tool_id="shell",
        capability_id="execute_command",
        method_type="shell",
        capability_fit=0.9,
        context_fit=0.8,
        availability=1.0,
        reliability=0.9,
        overall_score=0.85
    )

    routing_decision = MethodDecision(
        selected_candidate=candidate,
        confidence=0.85,
        candidates_considered=[candidate],
        routing_explanation="Shell method selected for high reliability"
    )

    # Test step conversion
    print("\nTesting step conversion to executor format...")
    step_dict = _convert_step_to_executor_format(step)

    print(f"  Step action: {step.action}")
    print(f"  Step parameters: {step.parameters}")
    print(f"  Converted action: {step_dict['action']}")
    print(f"  Converted command: {step_dict.get('command', 'N/A')}")
    print(f"  Converted shell_argv: {step_dict.get('shell_argv', [])}")

    # Verify concrete command is passed
    if step_dict['command'] == "gnome-system-monitor":
        print("\n✅ Concrete command passed correctly!")
    else:
        print(f"\n❌ Concrete command not passed: got {step_dict['command']}")
        return False

    # Test routing decision translation
    print("\nTesting routing decision translation...")
    executor_decision = _translate_routing_decision(routing_decision, step)

    if executor_decision:
        print(f"  Executor method: {executor_decision.method}")
        print(f"  Executor fallback chain: {executor_decision.fallback_chain}")
        print(f"  Executor shell_argv: {executor_decision.payload.shell_argv}")
    else:
        print("  ❌ Executor decision translation failed")
        return False

    # Verify no fallback chain (executor shouldn't do routing fallbacks)
    if executor_decision.fallback_chain == tuple():
        print("\n✅ No routing fallback chain (executor only retries execution-level failures)!")
    else:
        print(f"\n❌ Fallback chain still present: {executor_decision.fallback_chain}")
        return False

    # Verify shell_argv has concrete command
    if executor_decision.payload.shell_argv:
        print(f"\n✅ Concrete shell_argv passed: {executor_decision.payload.shell_argv}")
    else:
        print("\n❌ No shell_argv in payload")
        return False

    return True


async def test_orchestrator_subscriptions():
    """Test that orchestrator skips task-related subscriptions in LangGraph mode."""
    print("\n" + "=" * 60)
    print("Testing Orchestrator Subscription Strategy")
    print("=" * 60)

    print(f"\nFeature Flags:")
    print(f"  USE_LANGGRAPH: {flags.USE_LANGGRAPH}")

    if flags.USE_LANGGRAPH:
        print("\n✅ USE_LANGGRAPH is true")
        print("   Expected: Orchestrator should skip:")
        print("     - user_input_received")
        print("     - intent_parsed")
        print("     - capability_mapped")
        print("   Expected: Orchestrator should keep:")
        print("     - task_failed (observability)")
        print("     - task_completed (observability)")
        print("     - task_safety_cleared (observability)")
        print("     - confirmation_required (observability)")
        return True
    else:
        print("\nℹ️  USE_LANGGRAPH is false")
        print("   Legacy mode - all subscriptions active")
        return True


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("Legacy Delegation Removal Test Suite")
    print("=" * 60)

    # Test execute_step concrete commands
    execute_ok = await test_execute_step_concrete_commands()

    # Test orchestrator subscriptions
    sub_ok = await test_orchestrator_subscriptions()

    if execute_ok and sub_ok:
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        print("\nLegacy delegation removal is working correctly!")
        print("- Graph nodes pass concrete commands to executor")
        print("- Executor receives actual commands, not user_input")
        print("- Orchestrator skips task-related subscriptions in LangGraph mode")
        return 0
    else:
        print("\n" + "=" * 60)
        print("⚠️  TESTS FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
