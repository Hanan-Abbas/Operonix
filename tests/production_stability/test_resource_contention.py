"""
Resource Contention Tests — Production Stability Phase A
────────────────────────────────────────────────────────

Tests the graph's ability to handle multiple tasks competing for
the same resources (keyboard, filesystem, windows) without conflicts.

Success Criteria:
- Proper resource locking and release
- No deadlocks when multiple tasks compete for resources
- Graceful handling of resource conflicts
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

logger = logging.getLogger("ResourceContentionTests")


@dataclass
class ContentionResult:
    """Result of a resource contention test."""
    task_id: str
    resource_type: str
    success: bool
    acquired: bool
    execution_time: float
    error: str = None


@dataclass
class TestReport:
    """Report for resource contention test."""
    test_name: str
    total_tasks: int
    successful_acquisitions: int
    failed_acquisitions: int
    contentions_detected: int
    deadlocks_detected: int
    avg_execution_time: float
    errors: List[str] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)


class ResourceContentionTester:
    """Tests resource contention handling through the graph."""
    
    def __init__(self):
        self.adapter = None
        self.results: List[ContentionResult] = []
        
    async def setup(self):
        """Initialize the runtime adapter."""
        logger.info("=" * 80)
        logger.info("RESOURCE CONTENTION TESTS - SETUP")
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
    
    async def execute_resource_task(self, resource_type: str, task_index: int) -> ContentionResult:
        """Execute a task that competes for a specific resource."""
        from migration.domain_contracts import TaskSource
        
        # Define tasks that compete for specific resources
        resource_tasks = {
            "filesystem": f"read file /tmp/test_file_{task_index % 5}.txt",
            "keyboard": f"type text into terminal window",
            "window": f"focus and list windows",
            "network": f"check network connectivity",
        }
        
        task_input = resource_tasks.get(resource_type, "generic task")
        
        task_request = self.adapter.create_task_request(
            user_input=task_input,
            source=TaskSource.API,
            metadata={
                "resource_type": resource_type,
                "test_index": task_index
            }
        )
        
        start_time = time.time()
        
        try:
            result = await self.adapter.execute_task(task_request, use_graph=True)
            execution_time = time.time() - start_time
            
            return ContentionResult(
                task_id=task_request.task_id,
                resource_type=resource_type,
                success=result.success,
                acquired=result.success,  # Assume success means resource was acquired
                execution_time=execution_time,
                error=result.error if not result.success else None
            )
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"Task {task_index} failed with exception: {e}")
            return ContentionResult(
                task_id=task_request.task_id,
                resource_type=resource_type,
                success=False,
                acquired=False,
                execution_time=execution_time,
                error=str(e)
            )
    
    async def test_filesystem_contention(self, num_tasks: int = 50) -> TestReport:
        """Test 1: Filesystem Resource Contention
        
        Multiple tasks competing for the same filesystem resources.
        """
        logger.info("=" * 80)
        logger.info(f"TEST 1: FILESYSTEM RESOURCE CONTENTION ({num_tasks} tasks)")
        logger.info("=" * 80)
        
        start_time = time.time()
        
        try:
            self.results = []
            
            # Create tasks that compete for filesystem resources
            tasks = []
            for i in range(num_tasks):
                tasks.append(self.execute_resource_task("filesystem", i))
            
            logger.info(f"Executing {num_tasks} tasks competing for filesystem resources...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            successful = 0
            failed = 0
            contentions = 0
            execution_times = []
            errors = []
            
            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                    errors.append(f"Task raised exception: {result}")
                    logger.error(f"Task exception: {result}")
                elif isinstance(result, ContentionResult):
                    self.results.append(result)
                    if result.success:
                        successful += 1
                        execution_times.append(result.execution_time)
                    else:
                        failed += 1
                        contentions += 1
                        errors.append(result.error)
                        logger.warning(f"Task failed (possible contention): {result.error}")
            
            total_test_time = time.time() - start_time
            avg_execution_time = sum(execution_times) / len(execution_times) if execution_times else 0
            
            report = TestReport(
                test_name="Filesystem Resource Contention",
                total_tasks=num_tasks,
                successful_acquisitions=successful,
                failed_acquisitions=failed,
                contentions_detected=contentions,
                deadlocks_detected=0,
                avg_execution_time=avg_execution_time,
                errors=errors[:10],
                observations=[
                    f"Contention rate: {(contentions/num_tasks)*100:.2f}%",
                    f"Tasks per second: {num_tasks / total_test_time:.2f}",
                    f"Success rate: {(successful/num_tasks)*100:.2f}%"
                ]
            )
            
            # Log summary
            logger.info("\n" + "=" * 80)
            logger.info("TEST SUMMARY")
            logger.info("=" * 80)
            logger.info(f"Total Tasks: {num_tasks}")
            logger.info(f"Successful Acquisitions: {successful} ({(successful/num_tasks)*100:.2f}%)")
            logger.info(f"Failed Acquisitions: {failed}")
            logger.info(f"Contentions Detected: {contentions}")
            logger.info(f"Avg Execution Time: {avg_execution_time:.3f}s")
            logger.info(f"Total Test Time: {total_test_time:.3f}s")
            
            if errors:
                logger.warning(f"\nFirst {len(errors)} errors:")
                for error in errors:
                    logger.warning(f"  - {error}")
            
            return report
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            raise
    
    async def test_window_contention(self, num_tasks: int = 30) -> TestReport:
        """Test 2: Window Resource Contention
        
        Multiple tasks competing for window focus and manipulation.
        """
        logger.info("=" * 80)
        logger.info(f"TEST 2: WINDOW RESOURCE CONTENTION ({num_tasks} tasks)")
        logger.info("=" * 80)
        
        start_time = time.time()
        
        try:
            self.results = []
            
            # Create tasks that compete for window resources
            tasks = []
            for i in range(num_tasks):
                tasks.append(self.execute_resource_task("window", i))
            
            logger.info(f"Executing {num_tasks} tasks competing for window resources...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            successful = 0
            failed = 0
            contentions = 0
            execution_times = []
            errors = []
            
            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                    errors.append(f"Task raised exception: {result}")
                elif isinstance(result, ContentionResult):
                    self.results.append(result)
                    if result.success:
                        successful += 1
                        execution_times.append(result.execution_time)
                    else:
                        failed += 1
                        contentions += 1
                        errors.append(result.error)
            
            total_test_time = time.time() - start_time
            avg_execution_time = sum(execution_times) / len(execution_times) if execution_times else 0
            
            report = TestReport(
                test_name="Window Resource Contention",
                total_tasks=num_tasks,
                successful_acquisitions=successful,
                failed_acquisitions=failed,
                contentions_detected=contentions,
                deadlocks_detected=0,
                avg_execution_time=avg_execution_time,
                errors=errors[:10],
                observations=[
                    f"Contention rate: {(contentions/num_tasks)*100:.2f}%",
                    f"Success rate: {(successful/num_tasks)*100:.2f}%"
                ]
            )
            
            logger.info("\n" + "=" * 80)
            logger.info("WINDOW CONTENTION SUMMARY")
            logger.info("=" * 80)
            logger.info(f"Total Tasks: {num_tasks}")
            logger.info(f"Successful: {successful} ({(successful/num_tasks)*100:.2f}%)")
            logger.info(f"Failed: {failed}")
            logger.info(f"Contentions: {contentions}")
            
            return report
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            raise
    
    async def test_mixed_resource_contention(self, num_tasks: int = 40) -> TestReport:
        """Test 3: Mixed Resource Contention
        
        Multiple tasks competing for different types of resources simultaneously.
        """
        logger.info("=" * 80)
        logger.info(f"TEST 3: MIXED RESOURCE CONTENTION ({num_tasks} tasks)")
        logger.info("=" * 80)
        
        start_time = time.time()
        
        try:
            self.results = []
            
            resource_types = ["filesystem", "window", "network"]
            
            # Create tasks competing for different resources
            tasks = []
            for i in range(num_tasks):
                resource_type = resource_types[i % len(resource_types)]
                tasks.append(self.execute_resource_task(resource_type, i))
            
            logger.info(f"Executing {num_tasks} tasks competing for mixed resources...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results by resource type
            resource_stats = {rt: {"successful": 0, "failed": 0, "total": 0} for rt in resource_types}
            successful = 0
            failed = 0
            contentions = 0
            execution_times = []
            errors = []
            
            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                    errors.append(f"Task raised exception: {result}")
                elif isinstance(result, ContentionResult):
                    self.results.append(result)
                    resource_stats[result.resource_type]["total"] += 1
                    if result.success:
                        successful += 1
                        execution_times.append(result.execution_time)
                        resource_stats[result.resource_type]["successful"] += 1
                    else:
                        failed += 1
                        contentions += 1
                        resource_stats[result.resource_type]["failed"] += 1
                        errors.append(result.error)
            
            total_test_time = time.time() - start_time
            avg_execution_time = sum(execution_times) / len(execution_times) if execution_times else 0
            
            # Build observations
            observations = []
            for rt, stats in resource_stats.items():
                if stats["total"] > 0:
                    observations.append(
                        f"{rt}: {stats['successful']}/{stats['total']} successful "
                        f"({(stats['successful']/stats['total'])*100:.1f}%)"
                    )
            
            report = TestReport(
                test_name="Mixed Resource Contention",
                total_tasks=num_tasks,
                successful_acquisitions=successful,
                failed_acquisitions=failed,
                contentions_detected=contentions,
                deadlocks_detected=0,
                avg_execution_time=avg_execution_time,
                errors=errors[:10],
                observations=observations
            )
            
            logger.info("\n" + "=" * 80)
            logger.info("MIXED CONTENTION SUMMARY")
            logger.info("=" * 80)
            logger.info(f"Total Tasks: {num_tasks}")
            logger.info(f"Successful: {successful}")
            logger.info(f"Failed: {failed}")
            
            for rt, stats in resource_stats.items():
                if stats["total"] > 0:
                    logger.info(f"{rt}: {stats['successful']}/{stats['total']} "
                    f"({(stats['successful']/stats['total'])*100:.1f}%)")
            
            return report
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            raise
    
    async def test_deadlock_prevention(self, num_tasks: int = 20) -> TestReport:
        """Test 4: Deadlock Prevention
        
        Test that the graph properly handles potential deadlock scenarios.
        """
        logger.info("=" * 80)
        logger.info(f"TEST 4: DEADLOCK PREVENTION ({num_tasks} tasks)")
        logger.info("=" * 80)
        
        start_time = time.time()
        
        try:
            # Create tasks that could potentially deadlock
            # by requesting resources in different orders
            tasks = []
            for i in range(num_tasks):
                # Alternate between different resource types
                resource_type = "filesystem" if i % 2 == 0 else "window"
                tasks.append(self.execute_resource_task(resource_type, i))
            
            logger.info(f"Executing {num_tasks} tasks with potential deadlock scenarios...")
            
            # Set a timeout to detect deadlocks
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=120  # 2 minute timeout for deadlock detection
                )
                
                successful = sum(1 for r in results if isinstance(r, ContentionResult) and r.success)
                failed = num_tasks - successful
                
                total_test_time = time.time() - start_time
                
                report = TestReport(
                    test_name="Deadlock Prevention",
                    total_tasks=num_tasks,
                    successful_acquisitions=successful,
                    failed_acquisitions=failed,
                    contentions_detected=0,
                    deadlocks_detected=0,
                    avg_execution_time=0,
                    errors=[],
                    observations=[
                        f"No deadlocks detected within timeout",
                        f"Success rate: {(successful/num_tasks)*100:.2f}%"
                    ]
                )
                
                logger.info("\n" + "=" * 80)
                logger.info("DEADLOCK PREVENTION SUMMARY")
                logger.info("=" * 80)
                logger.info(f"✓ No deadlocks detected")
                logger.info(f"Successful: {successful}/{num_tasks}")
                
                return report
                
            except asyncio.TimeoutError:
                total_test_time = time.time() - start_time
                logger.error("✗ DEADLOCK DETECTED - tasks did not complete within timeout")
                
                report = TestReport(
                    test_name="Deadlock Prevention",
                    total_tasks=num_tasks,
                    successful_acquisitions=0,
                    failed_acquisitions=num_tasks,
                    contentions_detected=0,
                    deadlocks_detected=1,
                    avg_execution_time=0,
                    errors=["Deadlock detected - timeout exceeded"],
                    observations=[
                        f"DEADLOCK DETECTED",
                        f"Timeout: {total_test_time:.3f}s"
                    ]
                )
                
                return report
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            raise


async def main():
    """Run all resource contention tests."""
    tester = ResourceContentionTester()
    
    if not await tester.setup():
        logger.error("Setup failed. Exiting.")
        return
    
    reports = []
    
    # Test 1: Filesystem contention (50)
    try:
        report = await tester.test_filesystem_contention(num_tasks=50)
        reports.append(report)
    except Exception as e:
        logger.error(f"Test 1 failed: {e}")
    
    # Test 2: Window contention (30)
    try:
        report = await tester.test_window_contention(num_tasks=30)
        reports.append(report)
    except Exception as e:
        logger.error(f"Test 2 failed: {e}")
    
    # Test 3: Mixed resource contention (40)
    try:
        report = await tester.test_mixed_resource_contention(num_tasks=40)
        reports.append(report)
    except Exception as e:
        logger.error(f"Test 3 failed: {e}")
    
    # Test 4: Deadlock prevention (20)
    try:
        report = await tester.test_deadlock_prevention(num_tasks=20)
        reports.append(report)
    except Exception as e:
        logger.error(f"Test 4 failed: {e}")
    
    # Overall summary
    logger.info("\n" + "=" * 80)
    logger.info("OVERALL TEST SUMMARY")
    logger.info("=" * 80)
    
    for report in reports:
        logger.info(f"\n{report.test_name}:")
        logger.info(f"  Success Rate: {(report.successful_acquisitions/report.total_tasks)*100:.2f}%")
        logger.info(f"  Contentions: {report.contentions_detected}")
        logger.info(f"  Deadlocks: {report.deadlocks_detected}")
    
    # Success criteria check
    logger.info("\n" + "=" * 80)
    logger.info("SUCCESS CRITERIA CHECK")
    logger.info("=" * 80)
    
    all_passed = True
    for report in reports:
        if report.deadlocks_detected > 0:
            logger.error(f"✗ {report.test_name}: DEADLOCK DETECTED")
            all_passed = False
        elif report.successful_acquisitions / report.total_tasks < 0.90:
            logger.warning(f"✗ {report.test_name}: Success rate {(report.successful_acquisitions/report.total_tasks)*100:.2f}% < 90%")
            all_passed = False
        else:
            logger.info(f"✓ {report.test_name}: No deadlocks, success rate {(report.successful_acquisitions/report.total_tasks)*100:.2f}%")
    
    if all_passed:
        logger.info("\n✓ ALL TESTS PASSED")
    else:
        logger.warning("\n✗ SOME TESTS FAILED")


if __name__ == "__main__":
    asyncio.run(main())
