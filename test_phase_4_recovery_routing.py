"""
Test Phase 4 Step 4.3: Recovery Node Routing

Tests that recovery node routes to correct upstream stage based on failure category.
"""
import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from migration.domain_contracts import (
    ExecutionResult, FailureCategory, TaskStatus, TaskSource,
    RecoveryStrategy, RecoveryDecision, Plan, PlanStep, PlanStepIdempotency, PlanStepSideEffect
)
from graph.nodes.recover import _classify_failure, _determine_recovery_strategy, _get_target_stage
from migration.graph_state import OperonixState
from migration.domain_contracts import TaskRequest


def test_recovery_strategy_mapping():
    """Test that failure categories map to correct recovery strategies."""
    print("=" * 60)
    print("Test 1: Recovery Strategy Mapping")
    print("=" * 60)

    test_cases = [
        (FailureCategory.TRANSIENT, RecoveryStrategy.RETRY),
        (FailureCategory.CONTEXT_MISMATCH, RecoveryStrategy.OBSERVE),
        (FailureCategory.ROUTING_MISMATCH, RecoveryStrategy.ROUTE),
        (FailureCategory.TOOL_UNAVAILABLE, RecoveryStrategy.ROUTE),
        (FailureCategory.PLANNING_ERROR, RecoveryStrategy.REPLAN),
        (FailureCategory.PERMISSION_DENIED, RecoveryStrategy.ABORT),
        (FailureCategory.VALIDATION_REJECTED, RecoveryStrategy.ABORT),
        (FailureCategory.ENVIRONMENTAL, RecoveryStrategy.ABORT),
        (FailureCategory.PERMANENT, RecoveryStrategy.ABORT),
        (FailureCategory.UNKNOWN, RecoveryStrategy.ABORT),
    ]

    all_passed = True
    for failure_cat, expected_strategy in test_cases:
        # Create minimal state with a safe-to-retry plan
        task = TaskRequest(task_id="test", user_input="test", source=TaskSource.PANEL)
        step = PlanStep(
            step_id="step_1",
            objective="test objective",
            action="test action",
            idempotency=PlanStepIdempotency.IDEMPOTENT,
            side_effect=PlanStepSideEffect.NONE,
            reversibility=True
        )
        plan = Plan(
            plan_id="plan_1",
            steps=[step],
            current_step_index=0
        )
        state = OperonixState(
            task=task,
            plan=plan,
            intent=None,
            routing=None,
            execution=None,
            verification=None,
            recovery=None
        )

        strategy = _determine_recovery_strategy(failure_cat, state)
        if strategy == expected_strategy:
            print(f"  ✅ {failure_cat.value} → {strategy.value}")
        else:
            print(f"  ❌ {failure_cat.value} → {strategy.value} (expected {expected_strategy.value})")
            all_passed = False

    return all_passed


def test_target_stage_mapping():
    """Test that recovery strategies map to correct target stages."""
    print("\n" + "=" * 60)
    print("Test 2: Target Stage Mapping")
    print("=" * 60)

    test_cases = [
        (RecoveryStrategy.RETRY, "execute_step"),
        (RecoveryStrategy.OBSERVE, "observe"),
        (RecoveryStrategy.ROUTE, "route"),
        (RecoveryStrategy.REPLAN, "create_plan"),
        (RecoveryStrategy.ABORT, "finalize"),
    ]

    all_passed = True
    for strategy, expected_stage in test_cases:
        task = TaskRequest(task_id="test", user_input="test", source=TaskSource.PANEL)
        state = OperonixState(
            task=task,
            plan=None,
            intent=None,
            routing=None,
            execution=None,
            verification=None,
            recovery=None
        )

        stage = _get_target_stage(strategy, state)
        if stage == expected_stage:
            print(f"  ✅ {strategy.value} → {stage}")
        else:
            print(f"  ❌ {strategy.value} → {stage} (expected {expected_stage})")
            all_passed = False

    return all_passed


def test_full_recovery_flow():
    """Test full recovery flow from failure category to target stage."""
    print("\n" + "=" * 60)
    print("Test 3: Full Recovery Flow")
    print("=" * 60)

    test_cases = [
        (FailureCategory.TRANSIENT, RecoveryStrategy.RETRY, "execute_step"),
        (FailureCategory.CONTEXT_MISMATCH, RecoveryStrategy.OBSERVE, "observe"),
        (FailureCategory.ROUTING_MISMATCH, RecoveryStrategy.ROUTE, "route"),
        (FailureCategory.PLANNING_ERROR, RecoveryStrategy.REPLAN, "create_plan"),
    ]

    all_passed = True
    for failure_cat, expected_strategy, expected_stage in test_cases:
        # Create plan with safe-to-retry step
        step = PlanStep(
            step_id="step_1",
            objective="test objective",
            action="test action",
            idempotency=PlanStepIdempotency.IDEMPOTENT,
            side_effect=PlanStepSideEffect.NONE,
            reversibility=True
        )
        plan = Plan(
            plan_id="plan_1",
            steps=[step],
            current_step_index=0
        )

        task = TaskRequest(task_id="test", user_input="test", source=TaskSource.PANEL)
        state = OperonixState(
            task=task,
            plan=plan,
            intent=None,
            routing=None,
            execution=ExecutionResult(
                execution_id="test",
                step_id="step_1",
                success=False,
                method_used="shell",
                execution_status=TaskStatus.FAILED,
                result_data={"error": "Test"},
                failure_category=failure_cat
            ),
            verification=None,
            recovery=None
        )

        # Classify failure
        classified = _classify_failure(state)
        if classified != failure_cat:
            print(f"  ❌ Classification failed: {classified.value} != {failure_cat.value}")
            all_passed = False
            continue

        # Determine strategy
        strategy = _determine_recovery_strategy(classified, state)
        if strategy != expected_strategy:
            print(f"  ❌ Strategy failed: {strategy.value} != {expected_strategy.value}")
            all_passed = False
            continue

        # Get target stage
        stage = _get_target_stage(strategy, state)
        if stage != expected_stage:
            print(f"  ❌ Stage failed: {stage} != {expected_stage}")
            all_passed = False
            continue

        print(f"  ✅ {failure_cat.value} → {strategy.value} → {stage}")

    return all_passed


def test_no_universal_fallback():
    """Test that recovery doesn't use universal fallback chain."""
    print("\n" + "=" * 60)
    print("Test 4: No Universal Fallback Chain")
    print("=" * 60)

    # Recovery node should route to specific stages, not use fallback
    # This is verified by the target stage mapping test
    # Each failure category routes to a specific stage

    # Create plan with safe-to-retry step
    step = PlanStep(
        step_id="step_1",
        objective="test objective",
        action="test action",
        idempotency=PlanStepIdempotency.IDEMPOTENT,
        side_effect=PlanStepSideEffect.NONE,
        reversibility=True
    )
    plan = Plan(
        plan_id="plan_1",
        steps=[step],
        current_step_index=0
    )

    task = TaskRequest(task_id="test", user_input="test", source=TaskSource.PANEL)
    state = OperonixState(
        task=task,
        plan=plan,
        intent=None,
        routing=None,
        execution=None,
        verification=None,
        recovery=None
    )

    # Test that different failures go to different stages
    stages = set()
    for failure_cat in [FailureCategory.TRANSIENT, FailureCategory.ROUTING_MISMATCH, FailureCategory.PLANNING_ERROR]:
        strategy = _determine_recovery_strategy(failure_cat, state)
        stage = _get_target_stage(strategy, state)
        stages.add(stage)

    if len(stages) == 3:
        print(f"  ✅ Different failures route to different stages: {stages}")
        print(f"  ✅ No universal fallback chain detected")
        return True
    else:
        print(f"  ❌ All failures route to same stage: {stages}")
        return False


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("Phase 4 Step 4.3: Recovery Node Routing Tests")
    print("=" * 60)

    test1 = test_recovery_strategy_mapping()
    test2 = test_target_stage_mapping()
    test3 = test_full_recovery_flow()
    test4 = test_no_universal_fallback()

    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"Test 1 (Recovery Strategy Mapping): {'✅ PASSED' if test1 else '❌ FAILED'}")
    print(f"Test 2 (Target Stage Mapping): {'✅ PASSED' if test2 else '❌ FAILED'}")
    print(f"Test 3 (Full Recovery Flow): {'✅ PASSED' if test3 else '❌ FAILED'}")
    print(f"Test 4 (No Universal Fallback): {'✅ PASSED' if test4 else '❌ FAILED'}")

    if all([test1, test2, test3, test4]):
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        print("\nPhase 4 Step 4.3 is complete:")
        print("- Recovery routes to specific upstream stages")
        print("- No universal fallback chain")
        print("- Failure categories map to correct strategies")
        print("- Strategies map to correct target stages")
        return 0
    else:
        print("\n" + "=" * 60)
        print("⚠️  TESTS FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
