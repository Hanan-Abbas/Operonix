"""
Memory Leak Detection Tests — Production Stability Phase A
────────────────────────────────────────────────────────

Tests the graph's memory usage over extended runs to detect
memory leaks and ensure proper resource cleanup.

Success Criteria:
- Zero memory leaks over 24-hour continuous operation
- Memory growth rate < 10MB/hour
- Proper cleanup of graph state between tasks
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
import tracemalloc
import gc
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

logger = logging.getLogger("MemoryLeakDetectionTests")


@dataclass
class MemorySnapshot:
    """Snapshot of memory usage at a point in time."""
    timestamp: float
    current_memory: int
    peak_memory: int
    task_count: int
    garbage_collected: int


@dataclass
class TestReport:
    """Report for memory leak detection test."""
    test_name: str
    duration_seconds: float
    initial_memory_mb: float
    final_memory_mb: float
    memory_delta_mb: float
    memory_growth_rate_mb_per_hour: float
    peak_memory_mb: float
    total_tasks: int
    memory_leak_detected: bool
    snapshots: List[MemorySnapshot] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)


class MemoryLeakDetectionTester:
    """Tests memory usage and leak detection."""
    
    def __init__(self):
        self.adapter = None
        self.snapshots: List[MemorySnapshot] = []
        
    async def setup(self):
        """Initialize the runtime adapter."""
        logger.info("=" * 80)
        logger.info("MEMORY LEAK DETECTION TESTS - SETUP")
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
    
    async def execute_task(self, task_index: int):
        """Execute a single task."""
        from migration.domain_contracts import TaskSource
        
        task_request = self.adapter.create_task_request(
            user_input="check system status",
            source=TaskSource.API,
            metadata={"test_index": task_index}
        )
        
        try:
            result = await self.adapter.execute_task(task_request, use_graph=True)
            return result.success
        except Exception as e:
            logger.error(f"Task {task_index} failed: {e}")
            return False
    
    def take_memory_snapshot(self, task_count: int) -> MemorySnapshot:
        """Take a snapshot of current memory usage."""
        current, peak = tracemalloc.get_traced_memory()
        
        # Force garbage collection before snapshot
        collected = gc.collect()
        
        return MemorySnapshot(
            timestamp=time.time(),
            current_memory=current,
            peak_memory=peak,
            task_count=task_count,
            garbage_collected=collected
        )
    
    async def test_short_term_memory_growth(self, duration_seconds: int = 300, task_interval: float = 1.0) -> TestReport:
        """Test 1: Short-Term Memory Growth
        
        Monitor memory usage over a short period with continuous task execution.
        """
        logger.info("=" * 80)
        logger.info(f"TEST 1: SHORT-TERM MEMORY GROWTH ({duration_seconds}s)")
        logger.info("=" * 80)
        
        # Start memory tracking
        tracemalloc.start()
        initial_snapshot = self.take_memory_snapshot(0)
        self.snapshots = [initial_snapshot]
        
        start_time = time.time()
        task_count = 0
        
        try:
            logger.info(f"Running tasks for {duration_seconds} seconds...")
            
            while time.time() - start_time < duration_seconds:
                # Execute task
                success = await self.execute_task(task_count)
                task_count += 1
                
                # Take snapshot every 10 tasks or every 30 seconds
                if task_count % 10 == 0 or int(time.time() - start_time) % 30 == 0:
                    snapshot = self.take_memory_snapshot(task_count)
                    self.snapshots.append(snapshot)
                    
                    current_mb = snapshot.current_memory / 1024 / 1024
                    logger.info(f"Task {task_count}: Memory = {current_mb:.2f} MB, "
                               f"GC collected {snapshot.garbage_collected} objects")
                
                # Wait for interval
                await asyncio.sleep(task_interval)
            
            # Final snapshot
            final_snapshot = self.take_memory_snapshot(task_count)
            self.snapshots.append(final_snapshot)
            
            # Calculate statistics
            total_duration = time.time() - start_time
            initial_memory_mb = initial_snapshot.current_memory / 1024 / 1024
            final_memory_mb = final_snapshot.current_memory / 1024 / 1024
            peak_memory_mb = final_snapshot.peak_memory / 1024 / 1024
            memory_delta_mb = final_memory_mb - initial_memory_mb
            growth_rate_mb_per_hour = (memory_delta_mb / total_duration) * 3600 if total_duration > 0 else 0
            
            # Detect memory leak (growth > 10MB/hour)
            memory_leak_detected = growth_rate_mb_per_hour > 10.0
            
            report = TestReport(
                test_name="Short-Term Memory Growth",
                duration_seconds=total_duration,
                initial_memory_mb=initial_memory_mb,
                final_memory_mb=final_memory_mb,
                memory_delta_mb=memory_delta_mb,
                memory_growth_rate_mb_per_hour=growth_rate_mb_per_hour,
                peak_memory_mb=peak_memory_mb,
                total_tasks=task_count,
                memory_leak_detected=memory_leak_detected,
                snapshots=self.snapshots,
                observations=[
                    f"Tasks executed: {task_count}",
                    f"Tasks per second: {task_count / total_duration:.2f}",
                    f"Memory delta: {memory_delta_mb:.2f} MB",
                    f"Growth rate: {growth_rate_mb_per_hour:.2f} MB/hour",
                    f"Peak memory: {peak_memory_mb:.2f} MB"
                ]
            )
            
            # Log summary
            logger.info("\n" + "=" * 80)
            logger.info("MEMORY GROWTH SUMMARY")
            logger.info("=" * 80)
            logger.info(f"Duration: {total_duration:.1f}s")
            logger.info(f"Tasks Executed: {task_count}")
            logger.info(f"Initial Memory: {initial_memory_mb:.2f} MB")
            logger.info(f"Final Memory: {final_memory_mb:.2f} MB")
            logger.info(f"Memory Delta: {memory_delta_mb:.2f} MB")
            logger.info(f"Growth Rate: {growth_rate_mb_per_hour:.2f} MB/hour")
            logger.info(f"Peak Memory: {peak_memory_mb:.2f} MB")
            logger.info(f"Memory Leak Detected: {memory_leak_detected}")
            
            tracemalloc.stop()
            
            return report
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            tracemalloc.stop()
            raise
    
    async def test_memory_cleanup_between_tasks(self, num_tasks: int = 100) -> TestReport:
        """Test 2: Memory Cleanup Between Tasks
        
        Verify that memory is properly cleaned up between task executions.
        """
        logger.info("=" * 80)
        logger.info(f"TEST 2: MEMORY CLEANUP BETWEEN TASKS ({num_tasks} tasks)")
        logger.info("=" * 80)
        
        # Start memory tracking
        tracemalloc.start()
        initial_snapshot = self.take_memory_snapshot(0)
        self.snapshots = [initial_snapshot]
        
        start_time = time.time()
        memory_before_each_task = []
        
        try:
            logger.info(f"Executing {num_tasks} tasks with memory monitoring...")
            
            for i in range(num_tasks):
                # Force garbage collection before task
                gc.collect()
                memory_before = tracemalloc.get_traced_memory()[0]
                memory_before_each_task.append(memory_before)
                
                # Execute task
                success = await self.execute_task(i)
                
                # Force garbage collection after task
                gc.collect()
                memory_after = tracemalloc.get_traced_memory()[0]
                
                # Take snapshot every 20 tasks
                if (i + 1) % 20 == 0:
                    snapshot = self.take_memory_snapshot(i + 1)
                    self.snapshots.append(snapshot)
                    
                    before_mb = memory_before / 1024 / 1024
                    after_mb = memory_after / 1024 / 1024
                    logger.info(f"Task {i+1}: Before = {before_mb:.2f} MB, "
                               f"After = {after_mb:.2f} MB, "
                               f"Delta = {(after_mb - before_mb):.2f} MB")
            
            # Final snapshot
            final_snapshot = self.take_memory_snapshot(num_tasks)
            self.snapshots.append(final_snapshot)
            
            # Calculate statistics
            total_duration = time.time() - start_time
            initial_memory_mb = initial_snapshot.current_memory / 1024 / 1024
            final_memory_mb = final_snapshot.current_memory / 1024 / 1024
            peak_memory_mb = final_snapshot.peak_memory / 1024 / 1024
            memory_delta_mb = final_memory_mb - initial_memory_mb
            
            # Calculate average memory growth per task
            if len(memory_before_each_task) > 1:
                memory_growth_per_task = (memory_before_each_task[-1] - memory_before_each_task[0]) / len(memory_before_each_task)
                memory_growth_per_task_mb = memory_growth_per_task / 1024 / 1024
            else:
                memory_growth_per_task_mb = 0
            
            # Detect memory leak (growth > 1KB per task)
            memory_leak_detected = memory_growth_per_task_mb > 0.001
            
            report = TestReport(
                test_name="Memory Cleanup Between Tasks",
                duration_seconds=total_duration,
                initial_memory_mb=initial_memory_mb,
                final_memory_mb=final_memory_mb,
                memory_delta_mb=memory_delta_mb,
                memory_growth_rate_mb_per_hour=0,
                peak_memory_mb=peak_memory_mb,
                total_tasks=num_tasks,
                memory_leak_detected=memory_leak_detected,
                snapshots=self.snapshots,
                observations=[
                    f"Tasks executed: {num_tasks}",
                    f"Memory delta: {memory_delta_mb:.2f} MB",
                    f"Memory growth per task: {memory_growth_per_task_mb:.6f} MB",
                    f"Peak memory: {peak_memory_mb:.2f} MB"
                ]
            )
            
            logger.info("\n" + "=" * 80)
            logger.info("MEMORY CLEANUP SUMMARY")
            logger.info("=" * 80)
            logger.info(f"Tasks Executed: {num_tasks}")
            logger.info(f"Initial Memory: {initial_memory_mb:.2f} MB")
            logger.info(f"Final Memory: {final_memory_mb:.2f} MB")
            logger.info(f"Memory Delta: {memory_delta_mb:.2f} MB")
            logger.info(f"Memory Growth per Task: {memory_growth_per_task_mb:.6f} MB")
            logger.info(f"Peak Memory: {peak_memory_mb:.2f} MB")
            logger.info(f"Memory Leak Detected: {memory_leak_detected}")
            
            tracemalloc.stop()
            
            return report
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            tracemalloc.stop()
            raise
    
    async def test_peak_memory_stress(self, num_concurrent_tasks: int = 50) -> TestReport:
        """Test 3: Peak Memory Stress
        
        Test memory usage under high concurrency to identify peak memory requirements.
        """
        logger.info("=" * 80)
        logger.info(f"TEST 3: PEAK MEMORY STRESS ({num_concurrent_tasks} concurrent tasks)")
        logger.info("=" * 80)
        
        # Start memory tracking
        tracemalloc.start()
        initial_snapshot = self.take_memory_snapshot(0)
        self.snapshots = [initial_snapshot]
        
        start_time = time.time()
        
        try:
            # Create concurrent tasks
            tasks = []
            for i in range(num_concurrent_tasks):
                tasks.append(self.execute_task(i))
            
            logger.info(f"Executing {num_concurrent_tasks} tasks concurrently...")
            
            # Monitor memory during execution
            monitoring_task = asyncio.create_task(self._monitor_memory_during_execution(num_concurrent_tasks))
            
            # Execute all tasks
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Stop monitoring
            monitoring_task.cancel()
            try:
                await monitoring_task
            except asyncio.CancelledError:
                pass
            
            # Final snapshot
            final_snapshot = self.take_memory_snapshot(num_concurrent_tasks)
            self.snapshots.append(final_snapshot)
            
            # Calculate statistics
            total_duration = time.time() - start_time
            initial_memory_mb = initial_snapshot.current_memory / 1024 / 1024
            final_memory_mb = final_snapshot.current_memory / 1024 / 1024
            peak_memory_mb = final_snapshot.peak_memory / 1024 / 1024
            memory_delta_mb = final_memory_mb - initial_memory_mb
            
            successful = sum(1 for r in results if r is True)
            
            report = TestReport(
                test_name="Peak Memory Stress",
                duration_seconds=total_duration,
                initial_memory_mb=initial_memory_mb,
                final_memory_mb=final_memory_mb,
                memory_delta_mb=memory_delta_mb,
                memory_growth_rate_mb_per_hour=0,
                peak_memory_mb=peak_memory_mb,
                total_tasks=num_concurrent_tasks,
                memory_leak_detected=False,  # Stress test doesn't indicate leak
                snapshots=self.snapshots,
                observations=[
                    f"Concurrent tasks: {num_concurrent_tasks}",
                    f"Successful: {successful}",
                    f"Peak memory: {peak_memory_mb:.2f} MB",
                    f"Memory delta: {memory_delta_mb:.2f} MB"
                ]
            )
            
            logger.info("\n" + "=" * 80)
            logger.info("PEAK MEMORY STRESS SUMMARY")
            logger.info("=" * 80)
            logger.info(f"Concurrent Tasks: {num_concurrent_tasks}")
            logger.info(f"Successful: {successful}")
            logger.info(f"Initial Memory: {initial_memory_mb:.2f} MB")
            logger.info(f"Final Memory: {final_memory_mb:.2f} MB")
            logger.info(f"Peak Memory: {peak_memory_mb:.2f} MB")
            logger.info(f"Memory Delta: {memory_delta_mb:.2f} MB")
            
            tracemalloc.stop()
            
            return report
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            tracemalloc.stop()
            raise
    
    async def _monitor_memory_during_execution(self, expected_task_count: int):
        """Monitor memory during concurrent execution."""
        task_count = 0
        while True:
            await asyncio.sleep(0.5)
            snapshot = self.take_memory_snapshot(task_count)
            self.snapshots.append(snapshot)
            
            current_mb = snapshot.current_memory / 1024 / 1024
            peak_mb = snapshot.peak_memory / 1024 / 1024
            logger.info(f"Monitoring: Current = {current_mb:.2f} MB, Peak = {peak_mb:.2f} MB")
            
            task_count += 1


async def main():
    """Run all memory leak detection tests."""
    tester = MemoryLeakDetectionTester()
    
    if not await tester.setup():
        logger.error("Setup failed. Exiting.")
        return
    
    reports = []
    
    # Test 1: Short-term memory growth (5 minutes)
    try:
        report = await tester.test_short_term_memory_growth(duration_seconds=300, task_interval=1.0)
        reports.append(report)
    except Exception as e:
        logger.error(f"Test 1 failed: {e}")
    
    # Test 2: Memory cleanup between tasks (100 tasks)
    try:
        report = await tester.test_memory_cleanup_between_tasks(num_tasks=100)
        reports.append(report)
    except Exception as e:
        logger.error(f"Test 2 failed: {e}")
    
    # Test 3: Peak memory stress (50 concurrent tasks)
    try:
        report = await tester.test_peak_memory_stress(num_concurrent_tasks=50)
        reports.append(report)
    except Exception as e:
        logger.error(f"Test 3 failed: {e}")
    
    # Overall summary
    logger.info("\n" + "=" * 80)
    logger.info("OVERALL TEST SUMMARY")
    logger.info("=" * 80)
    
    for report in reports:
        logger.info(f"\n{report.test_name}:")
        logger.info(f"  Memory Delta: {report.memory_delta_mb:.2f} MB")
        logger.info(f"  Peak Memory: {report.peak_memory_mb:.2f} MB")
        logger.info(f"  Memory Leak Detected: {report.memory_leak_detected}")
    
    # Success criteria check
    logger.info("\n" + "=" * 80)
    logger.info("SUCCESS CRITERIA CHECK")
    logger.info("=" * 80)
    
    all_passed = True
    for report in reports:
        if report.memory_leak_detected:
            logger.error(f"✗ {report.test_name}: MEMORY LEAK DETECTED")
            all_passed = False
        elif report.memory_growth_rate_mb_per_hour > 10.0:
            logger.warning(f"✗ {report.test_name}: Growth rate {report.memory_growth_rate_mb_per_hour:.2f} MB/hour > 10 MB/hour")
            all_passed = False
        else:
            logger.info(f"✓ {report.test_name}: No memory leak detected")
    
    if all_passed:
        logger.info("\n✓ ALL TESTS PASSED")
    else:
        logger.warning("\n✗ SOME TESTS FAILED")


if __name__ == "__main__":
    asyncio.run(main())
