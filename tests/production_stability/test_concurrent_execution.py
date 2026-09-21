"""
Concurrent Execution Tests — Production Stability Phase A
──────────────────────────────────────────────────────────

Tests the graph's ability to handle multiple simultaneous tasks
with proper state isolation and no interference between tasks.

Success Criteria:
- 99.9% task completion rate across 100+ concurrent tasks
- Zero state corruption across concurrent tasks
- No deadlocks or race conditions
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
import tracemalloc
from datetime import datetime
from typing import List, Dict, Any
from dataclasses import dataclass, field
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('production_stability.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("ConcurrentExecutionTests")


@dataclass
class TaskResult:
    """Result of a single task execution."""
    task_id: str
    success: bool
    execution_time: float
    error: str = None
    state_snapshot: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TestReport:
    """Report for concurrent execution test."""
    test_name: str
    total_tasks: int
    successful_tasks: int
    failed_tasks: int
    completion_rate: float
    avg_execution_time: float
    min_execution_time: float
    max_execution_time: float
    total_test_time: float
    errors: List[str] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)


class ConcurrentExecutionTester:
    """Tests concurrent task execution through the graph."""
    
    def __init__(self):
        self.adapter = None
        self.results: List[TaskResult] = []
        
    async def setup(self):
        """Initialize the runtime adapter."""
        logger.info("=" * 80)
        logger.info("CONCURRENT EXECUTION TESTS - SETUP")
        logger.info("=" * 80)
        
        try:
            from graph.runtime_adapter import RuntimeGraphAdapter
            from migration.feature_flags import flags
            from migration.domain_contracts import TaskSource
            
            self.adapter = RuntimeGraphAdapter()
            
            logger.info("Feature Flags State:")
            for flag_name, flag_value in flags.get_all_flags().items():
                logger.info(f"  {flag_name}: {flag_value}")
            
            logger.info(f"Migration Phase: {flags.get_migration_phase()}")
            logger.info("✓ Runtime adapter initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"✗ Failed to initialize adapter: {e}", exc_info=True)
            return False
    
    async def execute_single_task(self, task_input: str, task_index: int) -> TaskResult:
        """Execute a single task and return result."""
        task_request = self.adapter.create_task_request(
            user_input=task_input,
            source=TaskSource.API,
            metadata={"test_index": task_index}
        )
        
        start_time = time.time()
        
        try:
            result = await self.adapter.execute_task(task_request, use_graph=True)
            execution_time = time.time() - start_time
            
            return TaskResult(
                task_id=task_request.task_id,
                success=result.success,
                execution_time=execution_time,
                error=result.error if not result.success else None,
                state_snapshot={
                    "response": result.response,
                    "paused": getattr(result, 'paused', False)
                }
            )
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"Task {task_index} failed with exception: {e}")
            return TaskResult(
                task_id=task_request.task_id,
                success=False,
                execution_time=execution_time,
                error=str(e)
            )
    
    async def test_concurrent_tasks(self, num_tasks: int = 100) -> TestReport:
        """Test 1: Concurrent Task Execution
        
        Run multiple tasks simultaneously to test graph state isolation.
        """
        logger.info("=" * 80)
        logger.info(f"TEST 1: CONCURRENT TASK EXECUTION ({num_tasks} tasks)")
        logger.info("=" * 80)
        
        start_time = time.time()
        self.results = []
        
        # Create task inputs (simple, non-destructive operations)
        task_inputs = [
            f"list files in /tmp",
            f"check system time",
            f"get current working directory",
            f"list environment variables",
        ]
        
        try:
            # Start memory tracking
            tracemalloc.start()
            initial_memory = tracemalloc.get_traced_memory()[0]
            
            # Create and execute all tasks concurrently
            tasks = []
            for i in range(num_tasks):
                task_input = task_inputs[i % len(task_inputs)]
                tasks.append(self.execute_single_task(task_input, i))
            
            logger.info(f"Executing {num_tasks} tasks concurrently...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            successful = 0
            failed = 0
            execution_times = []
            errors = []
            
            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                    errors.append(f"Task raised exception: {result}")
                    logger.error(f"Task exception: {result}")
                elif isinstance(result, TaskResult):
                    self.results.append(result)
                    if result.success:
                        successful += 1
                        execution_times.append(result.execution_time)
                    else:
                        failed += 1
                        errors.append(result.error)
                        logger.warning(f"Task failed: {result.error}")
            
            # Check memory usage
            final_memory = tracemalloc.get_traced_memory()[0]
            memory_delta = final_memory - initial_memory
            tracemalloc.stop()
            
            total_test_time = time.time() - start_time
            completion_rate = (successful / num_tasks) * 100 if num_tasks > 0 else 0
            
            avg_execution_time = sum(execution_times) / len(execution_times) if execution_times else 0
            min_execution_time = min(execution_times) if execution_times else 0
            max_execution_time = max(execution_times) if execution_times else 0
            
            report = TestReport(
                test_name="Concurrent Task Execution",
                total_tasks=num_tasks,
                successful_tasks=successful,
                failed_tasks=failed,
                completion_rate=completion_rate,
                avg_execution_time=avg_execution_time,
                min_execution_time=min_execution_time,
                max_execution_time=max_execution_time,
                total_test_time=total_test_time,
                errors=errors[:10],  # Limit to first 10 errors
                observations=[
                    f"Memory delta: {memory_delta / 1024 / 1024:.2f} MB",
                    f"Tasks per second: {num_tasks / total_test_time:.2f}",
                    f"Success rate: {completion_rate:.2f}%"
                ]
            )
            
            # Log summary
            logger.info("\n" + "=" * 80)
            logger.info("TEST SUMMARY")
            logger.info("=" * 80)
            logger.info(f"Total Tasks: {num_tasks}")
            logger.info(f"Successful: {successful} ({completion_rate:.2f}%)")
            logger.info(f"Failed: {failed}")
            logger.info(f"Avg Execution Time: {avg_execution_time:.3f}s")
            logger.info(f"Min Execution Time: {min_execution_time:.3f}s")
            logger.info(f"Max Execution Time: {max_execution_time:.3f}s")
            logger.info(f"Total Test Time: {total_test_time:.3f}s")
            logger.info(f"Memory Delta: {memory_delta / 1024 / 1024:.2f} MB")
            logger.info(f"Tasks/Second: {num_tasks / total_test_time:.2f}")
            
            if errors:
                logger.warning(f"\nFirst {len(errors)} errors:")
                for error in errors:
                    logger.warning(f"  - {error}")
            
            return report
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            raise
    
    async def test_state_isolation(self, num_tasks: int = 50) -> TestReport:
        """Test 2: State Isolation
        
        Verify that concurrent tasks do not interfere with each other's state.
        Each task should have independent state.
        """
        logger.info("=" * 80)
        logger.info(f"TEST 2: STATE ISOLATION ({num_tasks} tasks)")
        logger.info("=" * 80)
        
        start_time = time.time()
        
        try:
            # Use unique task inputs to ensure different states
            task_inputs = [f"test task number {i} with unique data {i * 100}" for i in range(num_tasks)]
            
            tasks = []
            for i, task_input in enumerate(task_inputs):
                tasks.append(self.execute_single_task(task_input, i))
            
            logger.info(f"Executing {num_tasks} unique tasks concurrently...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Verify state isolation
            successful = 0
            failed = 0
            state_corruptions = 0
            errors = []
            
            task_ids = set()
            
            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                    errors.append(f"Task raised exception: {result}")
                elif isinstance(result, TaskResult):
                    if result.success:
                        successful += 1
                        # Check for unique task IDs (state isolation)
                        if result.task_id in task_ids:
                            state_corruptions += 1
                            logger.error(f"Duplicate task ID detected: {result.task_id}")
                        task_ids.add(result.task_id)
                    else:
                        failed += 1
                        errors.append(result.error)
            
            total_test_time = time.time() - start_time
            completion_rate = (successful / num_tasks) * 100 if num_tasks > 0 else 0
            
            report = TestReport(
                test_name="State Isolation",
                total_tasks=num_tasks,
                successful_tasks=successful,
                failed_tasks=failed,
                completion_rate=completion_rate,
                avg_execution_time=0,
                min_execution_time=0,
                max_execution_time=0,
                total_test_time=total_test_time,
                errors=errors[:10],
                observations=[
                    f"State corruptions detected: {state_corruptions}",
                    f"Unique task IDs: {len(task_ids)}/{num_tasks}",
                    f"State isolation integrity: {(1 - state_corruptions/num_tasks)*100:.2f}%"
                ]
            )
            
            logger.info("\n" + "=" * 80)
            logger.info("STATE ISOLATION SUMMARY")
            logger.info("=" * 80)
            logger.info(f"Total Tasks: {num_tasks}")
            logger.info(f"Successful: {successful}")
            logger.info(f"Failed: {failed}")
            logger.info(f"State Corruptions: {state_corruptions}")
            logger.info(f"Unique Task IDs: {len(task_ids)}/{num_tasks}")
            logger.info(f"State Isolation Integrity: {(1 - state_corruptions/num_tasks)*100:.2f}%")
            
            return report
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            raise
    
    async def test_high_concurrency(self, num_tasks: int = 200) -> TestReport:
        """Test 3: High Concurrency Stress Test
        
        Test with a large number of concurrent tasks to identify
        any bottlenecks or resource limits.
        """
        logger.info("=" * 80)
        logger.info(f"TEST 3: HIGH CONCURRENCY STRESS ({num_tasks} tasks)")
        logger.info("=" * 80)
        
        start_time = time.time()
        
        try:
            # Simple tasks for stress testing
            task_inputs = ["check system status"] * num_tasks
            
            tasks = []
            for i in range(num_tasks):
                tasks.append(self.execute_single_task(task_inputs[i], i))
            
            logger.info(f"Executing {num_tasks} tasks concurrently (stress test)...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            successful = sum(1 for r in results if isinstance(r, TaskResult) and r.success)
            failed = num_tasks - successful
            completion_rate = (successful / num_tasks) * 100 if num_tasks > 0 else 0
            
            total_test_time = time.time() - start_time
            
            report = TestReport(
                test_name="High Concurrency Stress",
                total_tasks=num_tasks,
                successful_tasks=successful,
                failed_tasks=failed,
                completion_rate=completion_rate,
                avg_execution_time=0,
                min_execution_time=0,
                max_execution_time=0,
                total_test_time=total_test_time,
                errors=[],
                observations=[
                    f"Tasks per second: {num_tasks / total_test_time:.2f}",
                    f"Peak concurrency: {num_tasks}"
                ]
            )
            
            logger.info("\n" + "=" * 80)
            logger.info("HIGH CONCURRENCY SUMMARY")
            logger.info("=" * 80)
            logger.info(f"Total Tasks: {num_tasks}")
            logger.info(f"Successful: {successful} ({completion_rate:.2f}%)")
            logger.info(f"Failed: {failed}")
            logger.info(f"Total Test Time: {total_test_time:.3f}s")
            logger.info(f"Tasks/Second: {num_tasks / total_test_time:.2f}")
            
            return report
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            raise


async def main():
    """Run all concurrent execution tests."""
    tester = ConcurrentExecutionTester()
    
    if not await tester.setup():
        logger.error("Setup failed. Exiting.")
        return
    
    reports = []
    
    # Test 1: Concurrent tasks (100)
    try:
        report = await tester.test_concurrent_tasks(num_tasks=100)
        reports.append(report)
    except Exception as e:
        logger.error(f"Test 1 failed: {e}")
    
    # Test 2: State isolation (50)
    try:
        report = await tester.test_state_isolation(num_tasks=50)
        reports.append(report)
    except Exception as e:
        logger.error(f"Test 2 failed: {e}")
    
    # Test 3: High concurrency (200)
    try:
        report = await tester.test_high_concurrency(num_tasks=200)
        reports.append(report)
    except Exception as e:
        logger.error(f"Test 3 failed: {e}")
    
    # Overall summary
    logger.info("\n" + "=" * 80)
    logger.info("OVERALL TEST SUMMARY")
    logger.info("=" * 80)
    
    for report in reports:
        logger.info(f"\n{report.test_name}:")
        logger.info(f"  Completion Rate: {report.completion_rate:.2f}%")
        logger.info(f"  Total Time: {report.total_test_time:.3f}s")
    
    # Success criteria check
    logger.info("\n" + "=" * 80)
    logger.info("SUCCESS CRITERIA CHECK")
    logger.info("=" * 80)
    
    all_passed = True
    for report in reports:
        if report.completion_rate < 99.9:
            logger.warning(f"✗ {report.test_name}: Completion rate {report.completion_rate:.2f}% < 99.9%")
            all_passed = False
        else:
            logger.info(f"✓ {report.test_name}: Completion rate {report.completion_rate:.2f}% >= 99.9%")
    
    if all_passed:
        logger.info("\n✓ ALL TESTS PASSED")
    else:
        logger.warning("\n✗ SOME TESTS FAILED")


if __name__ == "__main__":
    asyncio.run(main())
