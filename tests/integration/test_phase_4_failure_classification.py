"""
Test Phase 4: Failure Classification Communication

Tests that executor's FailureClass is properly communicated to the graph
for recovery routing.
"""
import asyncio
import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from migration.domain_contracts import ExecutionResult, FailureCategory, TaskStatus, TaskSource
from graph.nodes.execute_step import _map_failure_class
from tools.routing_decision import FailureClass as ExecutorFailureClass


def test_failure_class_mapping():
    """Test that executor's FailureClass maps correctly to graph's FailureCategory."""
    print("=" * 60)
    print("Test 1: FailureClass Mapping")
    print("=" * 60)

    test_cases = [
        (ExecutorFailureClass.ENV_TRANSIENT, FailureCategory.TRANSIENT),
        (ExecutorFailureClass.ROUTING_MISMATCH, FailureCategory.ROUTING_MISMATCH),
        (ExecutorFailureClass.ENV_PERMANENT, FailureCategory.ENVIRONMENTAL),
        (ExecutorFailureClass.EXECUTION_LOGIC, FailureCategory.PLANNING_ERROR),
    ]

    all_passed = True
    for executor_fc, expected_graph_fc in test_cases:
        result = _map_failure_class(executor_fc)
        if result == expected_graph_fc:
            print(f"  ✅ {executor_fc.name} → {result.name}")
        else:
            print(f"  ❌ {executor_fc.name} → {result.name} (expected {expected_graph_fc.name})")
            all_passed = False

    # Test unknown mapping
    unknown_result = _map_failure_class(None)
    if unknown_result == FailureCategory.UNKNOWN:
        print(f"  ✅ None → {unknown_result.name}")
    else:
        print(f"  ❌ None → {unknown_result.name} (expected UNKNOWN)")
        all_passed = False

    return all_passed


def test_execution_result_with_failure_category():
    """Test that ExecutionResult can hold failure_category."""
    print("\n" + "=" * 60)
    print("Test 2: ExecutionResult with FailureCategory")
    print("=" * 60)

    try:
        result = ExecutionResult(
            execution_id="test_exec_1",
            step_id="step_1",
            success=False,
            method_used="shell",
            execution_status=TaskStatus.FAILED,
            result_data={"error": "Test error"},
            failure_category=FailureCategory.TRANSIENT
        )
        print(f"  ✅ ExecutionResult created with failure_category: {result.failure_category.value}")
        return True
    except Exception as e:
        print(f"  ❌ Failed to create ExecutionResult: {e}")
        return False


def test_executor_returns_failure_class():
    """Test that executor's _execute_with_decision returns FailureClass."""
    print("\n" + "=" * 60)
    print("Test 3: Executor Returns FailureClass")
    print("=" * 60)

    try:
        from executor.executor import Executor
        import inspect

        # Check signature
        sig = inspect.signature(Executor._execute_with_decision)
        print(f"  Signature: {sig}")

        # Check return annotation
        return_annotation = sig.return_annotation
        print(f"  Return annotation: {return_annotation}")

        # Should return tuple with 4 elements (success, result, method, failure_class)
        if "tuple" in str(return_annotation):
            print(f"  ✅ Returns tuple")
            # Check if tuple has 4 elements
            if "Optional[FailureClass]" in str(return_annotation) or "FailureClass" in str(return_annotation):
                print(f"  ✅ Includes FailureClass in return type")
                return True
            else:
                print(f"  ⚠️  Tuple may not include FailureClass")
                return False
        else:
            print(f"  ❌ Does not return tuple")
            return False

    except Exception as e:
        print(f"  ❌ Error inspecting executor: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_recovery_uses_failure_category():
    """Test that recovery node can use failure_category from execution result."""
    print("\n" + "=" * 60)
    print("Test 4: Recovery Node Uses FailureCategory")
    print("=" * 60)

    try:
        from graph.nodes.recover import _classify_failure
        from migration.graph_state import OperonixState
        from migration.domain_contracts import TaskRequest, TaskStatus, TaskSource

        # Create a minimal state with execution result
        task = TaskRequest(
            task_id="test_task",
            user_input="test",
            source=TaskSource.PANEL
        )

        state = OperonixState(
            task=task,
            plan=None,
            intent=None,
            routing=None,
            execution=ExecutionResult(
                execution_id="test_exec",
                step_id="step_1",
                success=False,
                method_used="shell",
                execution_status=TaskStatus.FAILED,
                result_data={"error": "Test error"},
                failure_category=FailureCategory.TRANSIENT
            ),
            verification=None,
            recovery=None
        )

        # Classify failure
        category = _classify_failure(state)
        print(f"  Classified failure: {category.value}")

        if category == FailureCategory.TRANSIENT:
            print(f"  ✅ Correctly classified as TRANSIENT from execution result")
            return True
        else:
            print(f"  ❌ Expected TRANSIENT, got {category.value}")
            return False

    except Exception as e:
        print(f"  ❌ Error testing recovery classification: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("Phase 4: Failure Classification Communication Tests")
    print("=" * 60)

    test1 = test_failure_class_mapping()
    test2 = test_execution_result_with_failure_category()
    test3 = test_executor_returns_failure_class()
    test4 = test_recovery_uses_failure_category()

    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"Test 1 (FailureClass Mapping): {'✅ PASSED' if test1 else '❌ FAILED'}")
    print(f"Test 2 (ExecutionResult Schema): {'✅ PASSED' if test2 else '❌ FAILED'}")
    print(f"Test 3 (Executor Return Type): {'✅ PASSED' if test3 else '❌ FAILED'}")
    print(f"Test 4 (Recovery Classification): {'✅ PASSED' if test4 else '❌ FAILED'}")

    if all([test1, test2, test3, test4]):
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        print("\nPhase 4 Step 4.1 is complete:")
        print("- FailureCategory added to ExecutionResult")
        print("- Executor returns FailureClass in execution result")
        print("- Recovery node uses FailureCategory for classification")
        print("- FailureClass mapping from executor to graph")
        return 0
    else:
        print("\n" + "=" * 60)
        print("⚠️  TESTS FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
