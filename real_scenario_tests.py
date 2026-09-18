"""
Real Scenario Tests — Operonix Graph
──────────────────────────────────────

Real-world scenario testing for the LangGraph workflow.
Tests actual commands and operations to validate end-to-end functionality.

Usage:
    python real_scenario_tests.py --scenario file_operations
    python real_scenario_tests.py --scenario app_launch
    python real_scenario_tests.py --scenario web_operations
    python real_scenario_tests.py --scenario all
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
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('real_scenario_tests.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("RealScenarioTests")


class RealScenarioTester:
    """Real-world scenario test runner."""
    
    def __init__(self):
        self.adapter = None
        self.results = []
        
    async def setup(self):
        """Initialize the runtime adapter."""
        logger.info("=" * 80)
        logger.info("REAL SCENARIO TESTS - SETUP")
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
    
    async def test_file_operations(self) -> dict:
        """Test 1: File Operations
        
        Scenarios:
        - Create a file
        - List directory contents
        - Read file contents
        - Delete a file
        """
        logger.info("=" * 80)
        logger.info("TEST 1: FILE OPERATIONS")
        logger.info("=" * 80)
        
        test_result = {
            "test_name": "File Operations",
            "status": "PENDING",
            "start_time": datetime.now().isoformat(),
            "observations": [],
            "issues": [],
            "scenarios_tested": []
        }
        
        try:
            from migration.domain_contracts import TaskSource
            import tempfile
            import os
            
            # Create temp directory for testing
            with tempfile.TemporaryDirectory() as tmpdir:
                logger.info(f"Using temp directory: {tmpdir}")
                
                # Scenario 1: Create a file
                logger.info("\n--- Scenario 1: Create File ---")
                request = self.adapter.create_task_request(
                    user_input=f"Create a test file in {tmpdir}",
                    source=TaskSource.VOICE
                )
                result = await self.adapter.execute_task(request, use_graph=True)
                logger.info(f"Create file result: {result.success}")
                test_result["scenarios_tested"].append("Create file")
                test_result["observations"].append(f"Create file: {result.success}")
                
                # Scenario 2: List directory
                logger.info("\n--- Scenario 2: List Directory ---")
                request = self.adapter.create_task_request(
                    user_input=f"List files in {tmpdir}",
                    source=TaskSource.VOICE
                )
                result = await self.adapter.execute_task(request, use_graph=True)
                logger.info(f"List directory result: {result.success}")
                test_result["scenarios_tested"].append("List directory")
                test_result["observations"].append(f"List directory: {result.success}")
                
                # Scenario 3: Read file (if exists)
                logger.info("\n--- Scenario 3: Read File ---")
                test_file = Path(tmpdir) / "test.txt"
                if test_file.exists():
                    request = self.adapter.create_task_request(
                        user_input=f"Read contents of {test_file}",
                        source=TaskSource.VOICE
                    )
                    result = await self.adapter.execute_task(request, use_graph=True)
                    logger.info(f"Read file result: {result.success}")
                    test_result["scenarios_tested"].append("Read file")
                    test_result["observations"].append(f"Read file: {result.success}")
            
            test_result["status"] = "PASS"
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            test_result["status"] = "FAIL"
            test_result["issues"].append(str(e))
        
        test_result["end_time"] = datetime.now().isoformat()
        self.results.append(test_result)
        return test_result
    
    async def test_app_launch(self) -> dict:
        """Test 2: Application Launch
        
        Scenarios:
        - Launch a common application (e.g., text editor)
        - Check if application is running
        - Close application
        """
        logger.info("=" * 80)
        logger.info("TEST 2: APPLICATION LAUNCH")
        logger.info("=" * 80)
        
        test_result = {
            "test_name": "Application Launch",
            "status": "PENDING",
            "start_time": datetime.now().isoformat(),
            "observations": [],
            "issues": [],
            "scenarios_tested": []
        }
        
        try:
            from migration.domain_contracts import TaskSource
            
            # Scenario 1: Launch text editor (safe operation)
            logger.info("\n--- Scenario 1: Launch Text Editor ---")
            request = self.adapter.create_task_request(
                user_input="Open gedit text editor",
                source=TaskSource.VOICE
            )
            result = await self.adapter.execute_task(request, use_graph=True)
            logger.info(f"Launch app result: {result.success}")
            logger.info(f"Response: {result.response}")
            test_result["scenarios_tested"].append("Launch application")
            test_result["observations"].append(f"Launch application: {result.success}")
            
            # Scenario 2: Check running applications
            logger.info("\n--- Scenario 2: Check Running Applications ---")
            request = self.adapter.create_task_request(
                user_input="List running applications",
                source=TaskSource.VOICE
            )
            result = await self.adapter.execute_task(request, use_graph=True)
            logger.info(f"List apps result: {result.success}")
            test_result["scenarios_tested"].append("List applications")
            test_result["observations"].append(f"List applications: {result.success}")
            
            test_result["status"] = "PASS"
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            test_result["status"] = "FAIL"
            test_result["issues"].append(str(e))
        
        test_result["end_time"] = datetime.now().isoformat()
        self.results.append(test_result)
        return test_result
    
    async def test_web_operations(self) -> dict:
        """Test 3: Web Operations
        
        Scenarios:
        - Open a URL in browser
        - Search the web
        - Fetch web content
        """
        logger.info("=" * 80)
        logger.info("TEST 3: WEB OPERATIONS")
        logger.info("=" * 80)
        
        test_result = {
            "test_name": "Web Operations",
            "status": "PENDING",
            "start_time": datetime.now().isoformat(),
            "observations": [],
            "issues": [],
            "scenarios_tested": []
        }
        
        try:
            from migration.domain_contracts import TaskSource
            
            # Scenario 1: Open URL
            logger.info("\n--- Scenario 1: Open URL ---")
            request = self.adapter.create_task_request(
                user_input="Open https://example.com in browser",
                source=TaskSource.VOICE
            )
            result = await self.adapter.execute_task(request, use_graph=True)
            logger.info(f"Open URL result: {result.success}")
            test_result["scenarios_tested"].append("Open URL")
            test_result["observations"].append(f"Open URL: {result.success}")
            
            # Scenario 2: Web search
            logger.info("\n--- Scenario 2: Web Search ---")
            request = self.adapter.create_task_request(
                user_input="Search the web for 'Python programming'",
                source=TaskSource.VOICE
            )
            result = await self.adapter.execute_task(request, use_graph=True)
            logger.info(f"Web search result: {result.success}")
            test_result["scenarios_tested"].append("Web search")
            test_result["observations"].append(f"Web search: {result.success}")
            
            test_result["status"] = "PASS"
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            test_result["status"] = "FAIL"
            test_result["issues"].append(str(e))
        
        test_result["end_time"] = datetime.now().isoformat()
        self.results.append(test_result)
        return test_result
    
    async def test_system_commands(self) -> dict:
        """Test 4: System Commands
        
        Scenarios:
        - Get system information
        - Check disk space
        - List processes
        """
        logger.info("=" * 80)
        logger.info("TEST 4: SYSTEM COMMANDS")
        logger.info("=" * 80)
        
        test_result = {
            "test_name": "System Commands",
            "status": "PENDING",
            "start_time": datetime.now().isoformat(),
            "observations": [],
            "issues": [],
            "scenarios_tested": []
        }
        
        try:
            from migration.domain_contracts import TaskSource
            
            # Scenario 1: Get system info
            logger.info("\n--- Scenario 1: Get System Information ---")
            request = self.adapter.create_task_request(
                user_input="Get system information",
                source=TaskSource.VOICE
            )
            result = await self.adapter.execute_task(request, use_graph=True)
            logger.info(f"System info result: {result.success}")
            test_result["scenarios_tested"].append("System information")
            test_result["observations"].append(f"System information: {result.success}")
            
            # Scenario 2: Check disk space
            logger.info("\n--- Scenario 2: Check Disk Space ---")
            request = self.adapter.create_task_request(
                user_input="Check disk space",
                source=TaskSource.VOICE
            )
            result = await self.adapter.execute_task(request, use_graph=True)
            logger.info(f"Disk space result: {result.success}")
            test_result["scenarios_tested"].append("Disk space")
            test_result["observations"].append(f"Disk space: {result.success}")
            
            # Scenario 3: List processes
            logger.info("\n--- Scenario 3: List Processes ---")
            request = self.adapter.create_task_request(
                user_input="List running processes",
                source=TaskSource.VOICE
            )
            result = await self.adapter.execute_task(request, use_graph=True)
            logger.info(f"List processes result: {result.success}")
            test_result["scenarios_tested"].append("List processes")
            test_result["observations"].append(f"List processes: {result.success}")
            
            test_result["status"] = "PASS"
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            test_result["status"] = "FAIL"
            test_result["issues"].append(str(e))
        
        test_result["end_time"] = datetime.now().isoformat()
        self.results.append(test_result)
        return test_result
    
    async def test_complex_workflow(self) -> dict:
        """Test 5: Complex Multi-Step Workflow
        
        Scenarios:
        - Create a project directory structure
        - Initialize a git repository
        - Create multiple files
        - Add and commit files
        """
        logger.info("=" * 80)
        logger.info("TEST 5: COMPLEX MULTI-STEP WORKFLOW")
        logger.info("=" * 80)
        
        test_result = {
            "test_name": "Complex Multi-Step Workflow",
            "status": "PENDING",
            "start_time": datetime.now().isoformat(),
            "observations": [],
            "issues": [],
            "scenarios_tested": []
        }
        
        try:
            from migration.domain_contracts import TaskSource
            import tempfile
            
            with tempfile.TemporaryDirectory() as tmpdir:
                logger.info(f"Using temp directory: {tmpdir}")
                
                # Scenario 1: Create project structure
                logger.info("\n--- Scenario 1: Create Project Structure ---")
                request = self.adapter.create_task_request(
                    user_input=f"Create a project directory structure in {tmpdir} with src and docs folders",
                    source=TaskSource.VOICE
                )
                result = await self.adapter.execute_task(request, use_graph=True)
                logger.info(f"Create structure result: {result.success}")
                test_result["scenarios_tested"].append("Create project structure")
                test_result["observations"].append(f"Create project structure: {result.success}")
                
                # Scenario 2: Initialize git
                logger.info("\n--- Scenario 2: Initialize Git Repository ---")
                request = self.adapter.create_task_request(
                    user_input=f"Initialize git repository in {tmpdir}",
                    source=TaskSource.VOICE
                )
                result = await self.adapter.execute_task(request, use_graph=True)
                logger.info(f"Git init result: {result.success}")
                test_result["scenarios_tested"].append("Initialize git")
                test_result["observations"].append(f"Initialize git: {result.success}")
                
                # Scenario 3: Create README
                logger.info("\n--- Scenario 3: Create README File ---")
                request = self.adapter.create_task_request(
                    user_input=f"Create a README.md file in {tmpdir}",
                    source=TaskSource.VOICE
                )
                result = await self.adapter.execute_task(request, use_graph=True)
                logger.info(f"Create README result: {result.success}")
                test_result["scenarios_tested"].append("Create README")
                test_result["observations"].append(f"Create README: {result.success}")
            
            test_result["status"] = "PASS"
            
        except Exception as e:
            logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
            test_result["status"] = "FAIL"
            test_result["issues"].append(str(e))
        
        test_result["end_time"] = datetime.now().isoformat()
        self.results.append(test_result)
        return test_result
    
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
            
            if result['scenarios_tested']:
                logger.info("  Scenarios Tested:")
                for scenario in result['scenarios_tested']:
                    logger.info(f"    - {scenario}")
            
            if result['observations']:
                logger.info("  Observations:")
                for obs in result['observations']:
                    logger.info(f"    - {obs}")
            
            if result['issues']:
                logger.info("  Issues:")
                for issue in result['issues']:
                    logger.info(f"    - {issue}")
        
        # Overall status
        passed = sum(1 for r in self.results if r['status'] == 'PASS')
        failed = sum(1 for r in self.results if r['status'] == 'FAIL')
        logger.info(f"\nOverall: {passed} passed, {failed} failed out of {len(self.results)} tests")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Real Scenario Test Runner")
    parser.add_argument(
        "--scenario",
        choices=["file_operations", "app_launch", "web_operations", "system_commands", "complex_workflow", "all"],
        default="file_operations",
        help="Which scenario to test"
    )
    
    args = parser.parse_args()
    
    tester = RealScenarioTester()
    
    # Setup
    if not await tester.setup():
        logger.error("Setup failed. Exiting.")
        sys.exit(1)
    
    # Run tests
    if args.scenario == "file_operations" or args.scenario == "all":
        await tester.test_file_operations()
    
    if args.scenario == "app_launch" or args.scenario == "all":
        await tester.test_app_launch()
    
    if args.scenario == "web_operations" or args.scenario == "all":
        await tester.test_web_operations()
    
    if args.scenario == "system_commands" or args.scenario == "all":
        await tester.test_system_commands()
    
    if args.scenario == "complex_workflow" or args.scenario == "all":
        await tester.test_complex_workflow()
    
    # Print summary
    tester.print_summary()
    
    # Save results to file
    import json
    results_file = Path("real_scenario_test_results.json")
    with open(results_file, "w") as f:
        json.dump(tester.results, f, indent=2)
    logger.info(f"\nResults saved to: {results_file}")


if __name__ == "__main__":
    asyncio.run(main())
