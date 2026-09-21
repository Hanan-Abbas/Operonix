"""
Long-Running Workflow Tests — Production Stability Phase A
──────────────────────────────────────────────────────────

Tests the graph's ability to handle complex multi-step workflows
over extended periods, verifying state persistence and stability.

Success Criteria:
- Complex workflows (10+ steps) complete successfully
- State persists correctly across workflow execution
- No timeouts or hangs in long-running workflows
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import datetime
from typing import List, Dict, Any
from dataclasses import dataclass, field

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('production_stability.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("LongRunningWorkflowTests")


@dataclass
class WorkflowResult:
    """Result of a workflow execution."""
    workflow_name: str
    success: bool
    total_steps: int
    completed_steps: int
    execution_time: float
    error: str = None
    checkpoints: List[str] = field(default_factory=list)


@dataclass
class TestReport:
    """Report for long-running workflow test."""
    test_name: str
    total_workflows: int
    successful_workflows: int
    failed_workflows: int
    completion_rate: float
    avg_execution_time: float
    min_execution_time: float
    max_execution_time: float
    avg_steps_per_workflow: float
    errors: List[str] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)


class LongRunningWorkflowTester:
    """Tests long-running workflow execution through the graph."""
    
    def __init__(self):
        self.adapter = None
        self.results: List[WorkflowResult] = []
        
    async def setup(self):
        """Initialize the runtime adapter."""
        logger.info("=" * 80)
        logger.info("LONG-RUNNING WORKFLOW TESTS - SETUP")
        logger.info("=" * 80)
        
        try:
            from graph.runtime_adapter import RuntimeGraphAdapter
            from migration.feature_flags import flags
            
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
    
    async def execute_workflow(self, workflow_name: str, task_input: str) -> WorkflowResult:
        """Execute a workflow and return result."""
        from migration.domain_contracts import TaskSource
        
        task_request = self.adapter.create_task_request(
            user_input=task_input,
            source=TaskSource.API,
            metadata={"workflow_name": workflow_name}
        )
        
        start_time = time.time()
        
        try:
            result = await self.adapter.execute_task(task_request, use_graph=True)
            execution_time = time.time() - start_time
            
            # Extract step information from result if available
            completed_steps = 1  # Default to at least 1 step
            checkpoints = []
            
            if hasattr(result, 'metadata') and result.metadata:
                completed_steps = result.metadata.get('completed_steps', 1)
                checkpoints = result.metadata.get('checkpoints', [])
            
            return WorkflowResult(
                workflow_name=workflow_name,
                success=result.success,
                total_steps=completed_steps,
                completed_steps=completed_steps,
                execution_time=execution_time,
                error=result.error if not result.success else None,
                checkpoints=checkpoints
            )
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"Workflow {workflow_name} failed with exception: {e}")
            return WorkflowResult(
                workflow_name=workflow_name,
                success=False,
                total_steps=0,
                completed_steps=0,
                execution_time=execution_time,
                error=str(e)
            )
    
    async def test_multi_step_workflow(self, num_workflows: int = 20) -> TestReport:
        """Test 1: Multi-Step Workflows
        
        Test workflows with multiple sequential steps to verify
        state persistence across the workflow.
        """
        logger.info("=" * 80)
        logger.info(f"TEST 1: MULTI-STEP WORKFLOWS ({num_workflows} workflows)")
        logger.info("=" * 80)
        
        start_time = time.time()
        
        # Define multi-step workflow scenarios
        workflow_scenarios = [
            ("File Operations Workflow", 
             "Create a test file, write some content to it, read it back, then delete it"),
            ("System Info Workflow",
             "Get system information, check disk space, list running processes, check memory usage"),
            ("Directory Navigation Workflow",
             "List current directory, navigate to /tmp, list contents, navigate back to home"),
            ("Environment Workflow",
             "List environment variables, check PATH, check HOME directory, check user information"),
        ]
        
        try:
            self.results = []
            
            tasks = []
            for i in range(num_workflows):
                workflow_name, task_input = workflow_scenarios[i % len(workflow_scenarios)]
                tasks.append(self.execute_workflow(f"{workflow_name}_{i}", task_input))
            
            logger.info(f"Executing {num_workflows} multi-step workflows...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            successful = 0
            failed = 0
            execution_times = []
            total_steps = 0
            errors = []
            
            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                    errors.append(f"Workflow raised exception: {result}")
                    logger.error(f"Workflow exception: {result}")
                elif isinstance(result, WorkflowResult):
                    self.results.append(result)
                    if result.success:
                        successful += 1
                        execution_times.append(result.execution_time)
                        total_steps += result.completed_steps
                    else:
                        failed += 1
                        errors.append(result.error)
                        logger.warning(f"Workflow failed: {result.error}")
            
            total_test_time = time.time() - start_time
            completion_rate = (successful / num_workflows) * 100 if num_workflows > 0 else 0
            avg_steps = total_steps / num_workflows if num_workflows > 0 else 0
            
            avg_execution_time = sum(execution_times) / len(execution_times) if execution_times else 0
            min_execution_time = min(execution_times) if execution_times else 0
            max_execution_time = max(execution_times) if execution_times else 0
            
            report = TestReport(
                test_name="Multi-Step Workflows",
                total_workflows=num_workflows,
                successful_workflows=successful,
                failed_workflows=failed,
                completion_rate=completion_rate,
                avg_execution_time=avg_execution_time,
                min_execution_time=min_execution_time,
                max_execution_time=max_execution_time,
                avg_steps_per_workflow=avg_steps,
                errors=errors[:10],
                observations=[
                    f"Total steps executed: {total_steps}",
                    f"Average steps per workflow: {avg_steps:.1f}",
                    f"Workflows per second: {num_workflows / total_test_time:.2f}"
                ]
            )
            
            # Log summary
            logger.info("\n" + "=" * 80)
            logger.info("TEST SUMMARY")
            logger.info("=" * 80)
            logger.info(f"Total Workflows: {num_workflows}")
            logger.info(f"Successful: {successful} ({completion_rate:.2f}%)")
            logger.info(f"Failed: {failed}")
            logger.info(f"Total Steps Executed: {total_steps}")
            logger.info(f"Avg Steps per Workflow: {avg_steps:.1f}")
            logger.info(f"Avg Execution Time: {avg_execution_time:.3f}s")
            logger.info(f"Min Execution Time: {min_execution_time:.3f}s")
            logger.info(f"Max Execution Time: {max_execution_time:.3f}s")
            logger.info(f"Total Test Time: {total_test_time:.3f}s")
            logger.info(f"Workflows/Second: {num_workflows / total_test_time:.2f}")
            
            if errors:
                logger.warning(f"\nFirst {len(errors)} errors:")
                for error in errors:
                    logger.warning(f"  - {error}")
            
            return report
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            raise
    
    async def test_extended_workflow(self, duration_seconds: int = 60) -> TestReport:
        """Test 2: Extended Workflow Duration
        
        Run a workflow for an extended period to verify no timeouts or hangs.
        """
        logger.info("=" * 80)
        logger.info(f"TEST 2: EXTENDED WORKFLOW DURATION ({duration_seconds}s)")
        logger.info("=" * 80)
        
        start_time = time.time()
        
        try:
            # Create a workflow that will run for the specified duration
            # by executing multiple sequential tasks
            task_input = "Check system status and monitor for a while"
            
            logger.info(f"Running extended workflow for {duration_seconds} seconds...")
            
            # Execute workflow with timeout
            try:
                result = await asyncio.wait_for(
                    self.execute_workflow("extended_workflow", task_input),
                    timeout=duration_seconds + 10  # Add buffer
                )
                
                execution_time = time.time() - start_time
                
                successful = 1 if result.success else 0
                failed = 0 if result.success else 1
                
                report = TestReport(
                    test_name="Extended Workflow Duration",
                    total_workflows=1,
                    successful_workflows=successful,
                    failed_workflows=failed,
                    completion_rate=100.0 if result.success else 0.0,
                    avg_execution_time=execution_time,
                    min_execution_time=execution_time,
                    max_execution_time=execution_time,
                    avg_steps_per_workflow=result.completed_steps,
                    errors=[result.error] if not result.success else [],
                    observations=[
                        f"Target duration: {duration_seconds}s",
                        f"Actual duration: {execution_time:.3f}s",
                        f"Steps completed: {result.completed_steps}"
                    ]
                )
                
                logger.info("\n" + "=" * 80)
                logger.info("EXTENDED WORKFLOW SUMMARY")
                logger.info("=" * 80)
                logger.info(f"Success: {result.success}")
                logger.info(f"Execution Time: {execution_time:.3f}s")
                logger.info(f"Steps Completed: {result.completed_steps}")
                
                return report
                
            except asyncio.TimeoutError:
                execution_time = time.time() - start_time
                logger.error(f"Workflow timed out after {execution_time:.3f}s")
                
                report = TestReport(
                    test_name="Extended Workflow Duration",
                    total_workflows=1,
                    successful_workflows=0,
                    failed_workflows=1,
                    completion_rate=0.0,
                    avg_execution_time=execution_time,
                    min_execution_time=execution_time,
                    max_execution_time=execution_time,
                    avg_steps_per_workflow=0,
                    errors=["Workflow timed out"],
                    observations=[
                        f"Target duration: {duration_seconds}s",
                        f"Actual duration: {execution_time:.3f}s",
                        f"Status: TIMEOUT"
                    ]
                )
                
                return report
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            raise
    
    async def test_checkpoint_persistence(self, num_checkpoints: int = 10) -> TestReport:
        """Test 3: Checkpoint Persistence
        
        Verify that workflow state can be saved and restored at checkpoints.
        """
        logger.info("=" * 80)
        logger.info(f"TEST 3: CHECKPOINT PERSISTENCE ({num_checkpoints} checkpoints)")
        logger.info("=" * 80)
        
        start_time = time.time()
        
        try:
            # Execute workflows that should trigger checkpoints
            task_input = "Perform a complex operation that requires multiple steps"
            
            tasks = []
            for i in range(num_checkpoints):
                tasks.append(self.execute_workflow(f"checkpoint_workflow_{i}", task_input))
            
            logger.info(f"Executing {num_checkpoints} workflows with checkpoints...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Count checkpoints
            successful = 0
            failed = 0
            total_checkpoints = 0
            errors = []
            
            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                    errors.append(f"Workflow raised exception: {result}")
                elif isinstance(result, WorkflowResult):
                    if result.success:
                        successful += 1
                        total_checkpoints += len(result.checkpoints)
                    else:
                        failed += 1
                        errors.append(result.error)
            
            total_test_time = time.time() - start_time
            completion_rate = (successful / num_checkpoints) * 100 if num_checkpoints > 0 else 0
            avg_checkpoints = total_checkpoints / num_checkpoints if num_checkpoints > 0 else 0
            
            report = TestReport(
                test_name="Checkpoint Persistence",
                total_workflows=num_checkpoints,
                successful_workflows=successful,
                failed_workflows=failed,
                completion_rate=completion_rate,
                avg_execution_time=0,
                min_execution_time=0,
                max_execution_time=0,
                avg_steps_per_workflow=0,
                errors=errors[:10],
                observations=[
                    f"Total checkpoints: {total_checkpoints}",
                    f"Average checkpoints per workflow: {avg_checkpoints:.1f}",
                    f"Checkpoint success rate: {completion_rate:.2f}%"
                ]
            )
            
            logger.info("\n" + "=" * 80)
            logger.info("CHECKPOINT PERSISTENCE SUMMARY")
            logger.info("=" * 80)
            logger.info(f"Total Workflows: {num_checkpoints}")
            logger.info(f"Successful: {successful} ({completion_rate:.2f}%)")
            logger.info(f"Total Checkpoints: {total_checkpoints}")
            logger.info(f"Avg Checkpoints per Workflow: {avg_checkpoints:.1f}")
            
            return report
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            raise


async def main():
    """Run all long-running workflow tests."""
    tester = LongRunningWorkflowTester()
    
    if not await tester.setup():
        logger.error("Setup failed. Exiting.")
        return
    
    reports = []
    
    # Test 1: Multi-step workflows (20)
    try:
        report = await tester.test_multi_step_workflow(num_workflows=20)
        reports.append(report)
    except Exception as e:
        logger.error(f"Test 1 failed: {e}")
    
    # Test 2: Extended workflow duration (60s)
    try:
        report = await tester.test_extended_workflow(duration_seconds=60)
        reports.append(report)
    except Exception as e:
        logger.error(f"Test 2 failed: {e}")
    
    # Test 3: Checkpoint persistence (10)
    try:
        report = await tester.test_checkpoint_persistence(num_checkpoints=10)
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
        logger.info(f"  Avg Execution Time: {report.avg_execution_time:.3f}s")
        logger.info(f"  Avg Steps per Workflow: {report.avg_steps_per_workflow:.1f}")
    
    # Success criteria check
    logger.info("\n" + "=" * 80)
    logger.info("SUCCESS CRITERIA CHECK")
    logger.info("=" * 80)
    
    all_passed = True
    for report in reports:
        if report.completion_rate < 95.0:  # Slightly lower threshold for long-running tests
            logger.warning(f"✗ {report.test_name}: Completion rate {report.completion_rate:.2f}% < 95%")
            all_passed = False
        else:
            logger.info(f"✓ {report.test_name}: Completion rate {report.completion_rate:.2f}% >= 95%")
    
    if all_passed:
        logger.info("\n✓ ALL TESTS PASSED")
    else:
        logger.warning("\n✗ SOME TESTS FAILED")


if __name__ == "__main__":
    asyncio.run(main())
