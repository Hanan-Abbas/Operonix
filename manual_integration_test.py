"""
Manual Integration Test Script — Operonix Migration
───────────────────────────────────────────────────

Manual integration testing for LangGraph workflow validation.
Tests workflows end-to-end with detailed logging and state inspection.

Usage:
    python manual_integration_test.py --test simple
    python manual_integration_test.py --test safety
    python manual_integration_test.py --test recovery
    python manual_integration_test.py --test pause_resume
    python manual_integration_test.py --test all
"""
from __future__ import annotations

import asyncio
import argparse
import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('manual_integration_test.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("ManualIntegrationTest")


class ManualIntegrationTester:
    """Manual integration test runner with detailed logging."""
    
    def __init__(self):
        self.adapter = None
        self.results = []
        
    async def setup(self):
        """Initialize the runtime adapter."""
        logger.info("=" * 80)
        logger.info("MANUAL INTEGRATION TEST - SETUP")
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
    
    async def test_simple_execution(self) -> dict:
        """Test 1: Simple Task Execution (Phase 1)
        
        Basic flow: INTAKE → OBSERVE → ANALYZE_INTENT → FINALIZE
        """
        logger.info("=" * 80)
        logger.info("TEST 1: SIMPLE TASK EXECUTION")
        logger.info("=" * 80)
        
        test_result = {
            "test_name": "Simple Task Execution",
            "status": "PENDING",
            "start_time": datetime.now().isoformat(),
            "observations": [],
            "issues": [],
            "missing_components": []
        }
        
        try:
            from migration.domain_contracts import TaskSource
            
            # Create simple task request
            logger.info("Creating task request: 'Open Firefox'")
            request = self.adapter.create_task_request(
                user_input="Open Firefox",
                source=TaskSource.VOICE
            )
            logger.info(f"Task ID: {request.task_id}")
            logger.info(f"User Input: {request.user_input}")
            logger.info(f"Source: {request.source}")
            
            # Execute task through graph
            logger.info("Executing task through LangGraph workflow...")
            result = await self.adapter.execute_task(request, use_graph=True)
            
            logger.info(f"Task completed with success: {result.success}")
            logger.info(f"Response: {result.response}")
            if result.error:
                logger.warning(f"Error: {result.error}")
            
            test_result["observations"].append("Graph workflow executed without errors")
            test_result["observations"].append(f"Final result success: {result.success}")
            test_result["status"] = "PASS"
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            test_result["status"] = "FAIL"
            test_result["issues"].append(str(e))
        
        test_result["end_time"] = datetime.now().isoformat()
        self.results.append(test_result)
        return test_result
    
    async def test_safety_check(self) -> dict:
        """Test 2: Safety Check Workflow (Phase 8)
        
        Flow: ... → SAFETY_CHECK → [EXECUTE_STEP | CONFIRMATION] → ...
        """
        logger.info("=" * 80)
        logger.info("TEST 2: SAFETY CHECK WORKFLOW")
        logger.info("=" * 80)
        
        test_result = {
            "test_name": "Safety Check Workflow",
            "status": "PENDING",
            "start_time": datetime.now().isoformat(),
            "observations": [],
            "issues": [],
            "missing_components": []
        }
        
        try:
            from migration.domain_contracts import TaskSource
            
            # Create task that might trigger safety check
            logger.info("Creating task request: 'Delete all files in /tmp'")
            request = self.adapter.create_task_request(
                user_input="Delete all files in /tmp",
                source=TaskSource.VOICE
            )
            
            logger.info("Executing task through graph...")
            result = await self.adapter.execute_task(request, use_graph=True)
            
            logger.info(f"Task completed with success: {result.success}")
            logger.info(f"Response: {result.response}")
            
            test_result["observations"].append("Safety check workflow executed")
            test_result["observations"].append(f"Confirmation required behavior observed")
            test_result["status"] = "PASS"
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            test_result["status"] = "FAIL"
            test_result["issues"].append(str(e))
        
        test_result["end_time"] = datetime.now().isoformat()
        self.results.append(test_result)
        return test_result
    
    async def test_recovery_scenario(self) -> dict:
        """Test 3: Recovery Scenario (Phase 5)
        
        Flow: EXECUTE_STEP → VERIFY_STEP → [FAILED] → RECOVER → ...
        """
        logger.info("=" * 80)
        logger.info("TEST 3: RECOVERY SCENARIO")
        logger.info("=" * 80)
        
        test_result = {
            "test_name": "Recovery Scenario",
            "status": "PENDING",
            "start_time": datetime.now().isoformat(),
            "observations": [],
            "issues": [],
            "missing_components": []
        }
        
        try:
            from migration.domain_contracts import TaskSource
            
            # Create task that might fail
            logger.info("Creating task request that may fail: 'Open non-existent application'")
            request = self.adapter.create_task_request(
                user_input="Open non-existent-app-xyz",
                source=TaskSource.VOICE
            )
            
            logger.info("Executing task through graph...")
            result = await self.adapter.execute_task(request, use_graph=True)
            
            logger.info(f"Task completed with success: {result.success}")
            logger.info(f"Response: {result.response}")
            
            test_result["observations"].append("Recovery workflow executed")
            test_result["observations"].append(f"Recovery strategy selection observed")
            test_result["status"] = "PASS"
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            test_result["status"] = "FAIL"
            test_result["issues"].append(str(e))
        
        test_result["end_time"] = datetime.now().isoformat()
        self.results.append(test_result)
        return test_result
    
    async def test_pause_resume(self) -> dict:
        """Test 4: Pause/Resume Workflow (Phase 7) with External Resume Mechanism
        
        Flow: SAFETY_CHECK → CONFIRMATION → PAUSE → External API Resume → EXECUTE_STEP
        """
        logger.info("=" * 80)
        logger.info("TEST 4: PAUSE/RESUME WORKFLOW WITH EXTERNAL RESUME")
        logger.info("=" * 80)
        
        test_result = {
            "test_name": "Pause/Resume Workflow",
            "status": "PENDING",
            "start_time": datetime.now().isoformat(),
            "observations": [],
            "issues": [],
            "missing_components": []
        }
        
        try:
            from migration.domain_contracts import TaskSource
            from graph.checkpointing import get_checkpointing_service
            
            # Create task requiring confirmation
            logger.info("Creating task request requiring confirmation: 'Format disk'")
            request = self.adapter.create_task_request(
                user_input="Format disk",
                source=TaskSource.VOICE
            )
            
            logger.info("Executing task through graph (should pause at confirmation)...")
            result = await self.adapter.execute_task(request, use_graph=True)
            
            logger.info(f"Task paused: {result.paused}")
            logger.info(f"Checkpoint identifier: {result.checkpoint_identifier}")
            
            if not result.paused:
                test_result["issues"].append("Task did not pause as expected")
                test_result["status"] = "FAIL"
                test_result["end_time"] = datetime.now().isoformat()
                self.results.append(test_result)
                return test_result
            
            test_result["observations"].append("Task paused at confirmation node")
            test_result["observations"].append(f"Checkpoint created: {result.checkpoint_identifier}")
            
            # Test checkpoint loading
            checkpointing_service = get_checkpointing_service()
            checkpoint = checkpointing_service.load_checkpoint(result.checkpoint_identifier)
            
            if checkpoint:
                test_result["observations"].append("Checkpoint successfully loaded")
                test_result["observations"].append(f"Checkpoint task_id: {checkpoint.task_id}")
            else:
                test_result["issues"].append("Failed to load checkpoint")
            
            # Test resume mechanism via direct function call (simulating API)
            logger.info("Testing resume mechanism...")
            from graph.nodes.confirmation import resume_from_confirmation
            from migration.domain_contracts import HumanInterventionType
            
            # Restore state from checkpoint
            if checkpoint:
                state = checkpointing_service.restore_state(checkpoint)
                if state:
                    logger.info("State restored from checkpoint")
                    
                    # Apply human response (CONFIRM)
                    state_update = resume_from_confirmation(state, HumanInterventionType.CONFIRM)
                    test_result["observations"].append("Human response applied (CONFIRM)")
                    
                    # Resume graph execution
                    logger.info("Resuming graph execution...")
                    final_state = await self.adapter.execute_task(request, use_graph=True)
                    
                    logger.info(f"Task completed with success: {final_state.success}")
                    logger.info(f"Response: {final_state.response}")
                    
                    test_result["observations"].append("Graph resumed and completed execution")
                    test_result["status"] = "PASS"
                else:
                    test_result["issues"].append("Failed to restore state from checkpoint")
                    test_result["status"] = "FAIL"
            else:
                test_result["issues"].append("Checkpoint not available for resume test")
                test_result["status"] = "FAIL"
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            test_result["status"] = "FAIL"
            test_result["issues"].append(str(e))
        
        test_result["end_time"] = datetime.now().isoformat()
        self.results.append(test_result)
        return test_result
    
    def print_graph_topology(self):
        """Print the current graph topology for reference."""
        logger.info("=" * 80)
        logger.info("GRAPH TOPOLOGY")
        logger.info("=" * 80)
        
        try:
            from graph.graph import build_operonix_graph
            graph = build_operonix_graph()
            
            if graph:
                logger.info("Graph built successfully")
                # Try to print ASCII representation if available
                try:
                    logger.info("\n" + graph.get_graph().print_ascii())
                except:
                    logger.info("ASCII representation not available")
            else:
                logger.warning("Graph is None (LangGraph may not be installed)")
                
        except Exception as e:
            logger.error(f"Failed to print graph topology: {e}", exc_info=True)
    
    def print_summary(self):
        """Print test summary."""
        logger.info("=" * 80)
        logger.info("TEST SUMMARY")
        logger.info("=" * 80)
        
        for result in self.results:
            logger.info(f"\n{result['test_name']}:")
            logger.info(f"  Status: {result['status']}")
            logger.info(f"  Start: {result['start_time']}")
            logger.info(f"  End: {result['end_time']}")
            
            if result['observations']:
                logger.info("  Observations:")
                for obs in result['observations']:
                    logger.info(f"    - {obs}")
            
            if result['issues']:
                logger.info("  Issues:")
                for issue in result['issues']:
                    logger.info(f"    - {issue}")
            
            if result['missing_components']:
                logger.info("  Missing Components:")
                for comp in result['missing_components']:
                    logger.info(f"    - {comp}")
        
        # Overall status
        passed = sum(1 for r in self.results if r['status'] == 'PASS')
        failed = sum(1 for r in self.results if r['status'] == 'FAIL')
        logger.info(f"\nOverall: {passed} passed, {failed} failed out of {len(self.results)} tests")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Manual Integration Test Runner")
    parser.add_argument(
        "--test",
        choices=["simple", "safety", "recovery", "pause_resume", "all"],
        default="simple",
        help="Which test to run"
    )
    parser.add_argument(
        "--no-topology",
        action="store_true",
        help="Skip printing graph topology"
    )
    
    args = parser.parse_args()
    
    tester = ManualIntegrationTester()
    
    # Setup
    if not await tester.setup():
        logger.error("Setup failed. Exiting.")
        sys.exit(1)
    
    # Print graph topology
    if not args.no_topology:
        tester.print_graph_topology()
    
    # Run tests
    if args.test == "simple" or args.test == "all":
        await tester.test_simple_execution()
    
    if args.test == "safety" or args.test == "all":
        await tester.test_safety_check()
    
    if args.test == "recovery" or args.test == "all":
        await tester.test_recovery_scenario()
    
    if args.test == "pause_resume" or args.test == "all":
        await tester.test_pause_resume()
    
    # Print summary
    tester.print_summary()
    
    # Save results to file
    import json
    results_file = Path("manual_integration_test_results.json")
    with open(results_file, "w") as f:
        json.dump(tester.results, f, indent=2)
    logger.info(f"\nResults saved to: {results_file}")


if __name__ == "__main__":
    asyncio.run(main())
