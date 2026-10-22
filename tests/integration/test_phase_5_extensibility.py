"""
Test Phase 5: Candidate Discovery Extensibility

Tests that new method types can be registered without modifying core code.
"""
import asyncio
import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from migration.domain_contracts import Candidate, CandidateType, PlanStep, PlanStepIdempotency, PlanStepSideEffect, TaskSource, TaskRequest
from graph.candidate_discovery import get_candidate_discovery_service


def test_builtin_discovery_functions():
    """Test that built-in discovery functions are registered."""
    print("=" * 60)
    print("Test 1: Built-in Discovery Functions Registered")
    print("=" * 60)

    service = get_candidate_discovery_service()

    expected_methods = ["shell", "api", "plugin", "ui", "browser_automation", "vision"]
    registered_methods = list(service.custom_discovery_functions.keys())

    all_passed = True
    for method in expected_methods:
        if method in registered_methods:
            print(f"  ✅ {method} discovery function registered")
        else:
            print(f"  ❌ {method} discovery function NOT registered")
            all_passed = False

    print(f"  Total registered: {len(registered_methods)}")
    return all_passed


def test_custom_method_registration():
    """Test that a custom method type can be registered."""
    print("\n" + "=" * 60)
    print("Test 2: Custom Method Registration")
    print("=" * 60)

    service = get_candidate_discovery_service()

    # Define a custom discovery function for a hypothetical "mcp" method
    def discover_mcp_candidates(plan_step, intent, context):
        """Custom discovery function for MCP method type."""
        candidates = []
        # In a real implementation, this would check available MCP servers
        # For testing, we just return a mock candidate
        candidate = Candidate(
            candidate_type=CandidateType.REMOTE,  # Use REMOTE as proxy
            tool_id="mcp_server",
            capability_id="mcp_execute",
            metadata={"custom_method": "mcp"}
        )
        candidates.append(candidate)
        return candidates

    # Register the custom discovery function
    service.register_candidate_discovery("mcp", discover_mcp_candidates)

    # Verify it's registered
    if "mcp" in service.custom_discovery_functions:
        print(f"  ✅ Custom 'mcp' discovery function registered")
        return True
    else:
        print(f"  ❌ Custom 'mcp' discovery function NOT registered")
        return False


def test_custom_method_discovery():
    """Test that custom discovery functions are called during discovery."""
    print("\n" + "=" * 60)
    print("Test 3: Custom Method Discovery")
    print("=" * 60)

    service = get_candidate_discovery_service()

    # Define a custom discovery function
    def discover_custom_method(plan_step, intent, context):
        """Custom discovery function for testing."""
        candidates = []
        candidate = Candidate(
            candidate_type=CandidateType.REMOTE,
            tool_id="custom_tool",
            capability_id="custom_capability",
            metadata={"custom": True}
        )
        candidates.append(candidate)
        return candidates

    # Register the custom discovery function
    service.register_candidate_discovery("custom_method", discover_custom_method)

    # Create a plan step
    step = PlanStep(
        step_id="step_1",
        objective="test objective",
        action="test action",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        reversibility=True
    )

    # Discover candidates
    candidates = service.discover_candidates(step, None, None)

    # Check if custom candidate is included
    custom_candidates = [c for c in candidates if c.tool_id == "custom_tool"]
    if len(custom_candidates) > 0:
        print(f"  ✅ Custom discovery function called: {len(custom_candidates)} custom candidates found")
        print(f"  ✅ Total candidates: {len(candidates)}")
        return True
    else:
        print(f"  ❌ Custom discovery function NOT called")
        print(f"  Total candidates: {len(candidates)}")
        return False


def test_builtin_methods_still_work():
    """Test that built-in methods still work after registration mechanism changes."""
    print("\n" + "=" * 60)
    print("Test 4: Built-in Methods Still Work")
    print("=" * 60)

    service = get_candidate_discovery_service()

    # Create a plan step
    step = PlanStep(
        step_id="step_1",
        objective="execute command",
        action="test command",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        reversibility=True
    )

    # Discover candidates
    candidates = service.discover_candidates(step, None, None)

    # Check for shell candidate (should always be present)
    shell_candidates = [c for c in candidates if c.candidate_type == CandidateType.SHELL]
    if len(shell_candidates) > 0:
        print(f"  ✅ Shell candidate found: {len(shell_candidates)}")
        print(f"  ✅ Total candidates: {len(candidates)}")
        return True
    else:
        print(f"  ❌ Shell candidate NOT found")
        print(f"  Total candidates: {len(candidates)}")
        return False


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("Phase 5: Candidate Discovery Extensibility Tests")
    print("=" * 60)

    test1 = test_builtin_discovery_functions()
    test2 = test_custom_method_registration()
    test3 = test_custom_method_discovery()
    test4 = test_builtin_methods_still_work()

    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"Test 1 (Built-in Functions): {'✅ PASSED' if test1 else '❌ FAILED'}")
    print(f"Test 2 (Custom Registration): {'✅ PASSED' if test2 else '❌ FAILED'}")
    print(f"Test 3 (Custom Discovery): {'✅ PASSED' if test3 else '❌ FAILED'}")
    print(f"Test 4 (Built-in Still Work): {'✅ PASSED' if test4 else '❌ FAILED'}")

    if all([test1, test2, test3, test4]):
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        print("\nPhase 5 is complete:")
        print("- Built-in discovery functions registered")
        print("- Custom method types can be registered")
        print("- Custom discovery functions are called")
        print("- Built-in methods still work")
        print("- No hardcoded method type list")
        print("- New methods can be added without code changes")
        return 0
    else:
        print("\n" + "=" * 60)
        print("⚠️  TESTS FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
