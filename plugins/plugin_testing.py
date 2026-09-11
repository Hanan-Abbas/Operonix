"""
Plugin Testing Framework — Operonix Plugin System
──────────────────────────────────────────────────

Plugin testing framework for Operonix plugins.
Per migration plan Phase 12: Plugin Integration

This framework provides testing utilities for plugins to ensure they work
correctly within the Operonix system.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("Plugins.PluginTesting")


class TestResult(str, Enum):
    """Test result status."""
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class TestCase:
    """Test case for a plugin."""
    name: str
    description: str
    test_function: Callable
    expected_result: Any
    parameters: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.parameters is None:
            self.parameters = {}


@dataclass
class TestExecution:
    """Execution result of a test case."""
    test_case: TestCase
    result: TestResult
    actual_result: Any
    error_message: Optional[str] = None
    execution_time: float = 0.0


class PluginTester:
    """Tester for Operonix plugins.
    
    This class provides testing utilities for plugins to ensure they work
    correctly within the Operonix system.
    
    Per migration plan Phase 12: Plugin Integration
    """
    
    def __init__(self, plugin: Any, manifest: Any = None):
        """Initialize the plugin tester.
        
        Args:
            plugin: Plugin instance to test
            manifest: Plugin manifest (optional)
        """
        self.plugin = plugin
        self.manifest = manifest
        self.test_cases: List[TestCase] = []
        logger.info(f"PluginTester initialized for plugin: {plugin}")
    
    def add_test_case(self, test_case: TestCase) -> None:
        """Add a test case.
        
        Args:
            test_case: Test case to add
        """
        self.test_cases.append(test_case)
        logger.info(f"Added test case: {test_case.name}")
    
    def run_test(self, test_case: TestCase) -> TestExecution:
        """Run a single test case.
        
        Args:
            test_case: Test case to run
            
        Returns:
            TestExecution result
        """
        import time
        
        logger.info(f"Running test case: {test_case.name}")
        
        start_time = time.time()
        
        try:
            # Execute test function
            actual_result = test_case.test_function(self.plugin, **test_case.parameters)
            
            # Check if result matches expected
            if actual_result == test_case.expected_result:
                result = TestResult.PASSED
                error_message = None
            else:
                result = TestResult.FAILED
                error_message = f"Expected {test_case.expected_result}, got {actual_result}"
            
        except Exception as e:
            result = TestResult.ERROR
            actual_result = None
            error_message = str(e)
            logger.error(f"Error running test case {test_case.name}: {e}")
        
        execution_time = time.time() - start_time
        
        execution = TestExecution(
            test_case=test_case,
            result=result,
            actual_result=actual_result,
            error_message=error_message,
            execution_time=execution_time
        )
        
        logger.info(f"Test case {test_case.name} completed: {result.value} ({execution_time:.3f}s)")
        
        return execution
    
    def run_all_tests(self) -> List[TestExecution]:
        """Run all test cases.
        
        Returns:
            List of TestExecution results
        """
        logger.info(f"Running {len(self.test_cases)} test cases")
        
        executions = []
        
        for test_case in self.test_cases:
            execution = self.run_test(test_case)
            executions.append(execution)
        
        return executions
    
    def get_test_summary(self, executions: List[TestExecution]) -> Dict[str, Any]:
        """Get summary of test results.
        
        Args:
            executions: List of test executions
            
        Returns:
            Dict with test summary
        """
        total = len(executions)
        passed = sum(1 for e in executions if e.result == TestResult.PASSED)
        failed = sum(1 for e in executions if e.result == TestResult.FAILED)
        skipped = sum(1 for e in executions if e.result == TestResult.SKIPPED)
        error = sum(1 for e in executions if e.result == TestResult.ERROR)
        
        total_time = sum(e.execution_time for e in executions)
        
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "error": error,
            "success_rate": passed / total if total > 0 else 0.0,
            "total_time": total_time
        }
    
    def validate_manifest(self) -> Dict[str, Any]:
        """Validate plugin manifest.
        
        Phase 12 enhancement: Validate plugin manifest structure and content.
        
        Returns:
            Dict with validation results
        """
        if not self.manifest:
            return {
                "valid": False,
                "errors": ["No manifest provided"]
            }
        
        errors = []
        warnings = []
        
        # Check required fields
        if not self.manifest.plugin_id:
            errors.append("Missing plugin_id")
        
        if not self.manifest.name:
            errors.append("Missing name")
        
        if not self.manifest.version:
            errors.append("Missing version")
        
        if not self.manifest.description:
            errors.append("Missing description")
        
        # Check capabilities
        if not self.manifest.capabilities:
            warnings.append("No capabilities defined")
        
        for capability in self.manifest.capabilities:
            if not capability.capability_id:
                errors.append(f"Capability missing capability_id")
            
            if not capability.name:
                errors.append(f"Capability {capability.capability_id} missing name")
            
            if not capability.description:
                warnings.append(f"Capability {capability.capability_id} missing description")
        
        # Check permissions
        if self.manifest.permissions:
            for permission in self.manifest.permissions:
                # Check if permission is a valid PluginPermission
                from plugins.plugin_manifest import PluginPermission
                if permission not in PluginPermission:
                    warnings.append(f"Unknown permission: {permission}")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings
        }
    
    def test_capability(self, capability_id: str, parameters: Dict[str, Any]) -> TestExecution:
        """Test a specific capability.
        
        Args:
            capability_id: Capability identifier
            parameters: Parameters for the capability
            
        Returns:
            TestExecution result
        """
        logger.info(f"Testing capability: {capability_id}")
        
        # Create test case for capability
        test_case = TestCase(
            name=f"capability_{capability_id}",
            description=f"Test capability {capability_id}",
            test_function=lambda plugin, **params: plugin.execute(**params),
            expected_result={"success": True},
            parameters=parameters
        )
        
        return self.run_test(test_case)


class PluginTestSuite:
    """Test suite for multiple plugins.
    
    This class manages testing for multiple plugins and provides aggregated results.
    """
    
    def __init__(self):
        """Initialize the plugin test suite."""
        self.testers: Dict[str, PluginTester] = {}
        logger.info("PluginTestSuite initialized")
    
    def add_plugin(self, plugin_id: str, plugin: Any, manifest: Any = None) -> PluginTester:
        """Add a plugin to the test suite.
        
        Args:
            plugin_id: Plugin identifier
            plugin: Plugin instance
            manifest: Plugin manifest (optional)
            
        Returns:
            PluginTester instance
        """
        tester = PluginTester(plugin, manifest)
        self.testers[plugin_id] = tester
        logger.info(f"Added plugin to test suite: {plugin_id}")
        return tester
    
    def run_all_tests(self) -> Dict[str, List[TestExecution]]:
        """Run all tests for all plugins.
        
        Returns:
            Dict mapping plugin IDs to test executions
        """
        logger.info(f"Running tests for {len(self.testers)} plugins")
        
        results = {}
        
        for plugin_id, tester in self.testers.items():
            executions = tester.run_all_tests()
            results[plugin_id] = executions
        
        return results
    
    def get_aggregated_summary(self, results: Dict[str, List[TestExecution]]) -> Dict[str, Any]:
        """Get aggregated summary of all test results.
        
        Args:
            results: Dict mapping plugin IDs to test executions
            
        Returns:
            Dict with aggregated summary
        """
        total_tests = sum(len(executions) for executions in results.values())
        total_passed = sum(
            sum(1 for e in executions if e.result == TestResult.PASSED)
            for executions in results.values()
        )
        total_failed = sum(
            sum(1 for e in executions if e.result == TestResult.FAILED)
            for executions in results.values()
        )
        total_skipped = sum(
            sum(1 for e in executions if e.result == TestResult.SKIPPED)
            for executions in results.values()
        )
        total_error = sum(
            sum(1 for e in executions if e.result == TestResult.ERROR)
            for executions in results.values()
        )
        
        total_time = sum(
            sum(e.execution_time for e in executions)
            for executions in results.values()
        )
        
        return {
            "total_plugins": len(results),
            "total_tests": total_tests,
            "total_passed": total_passed,
            "total_failed": total_failed,
            "total_skipped": total_skipped,
            "total_error": total_error,
            "overall_success_rate": total_passed / total_tests if total_tests > 0 else 0.0,
            "total_time": total_time
        }


# Global plugin test suite instance
_plugin_test_suite: Optional[PluginTestSuite] = None


def get_plugin_test_suite() -> PluginTestSuite:
    """Get the global plugin test suite instance.
    
    Returns:
        PluginTestSuite instance
    """
    global _plugin_test_suite
    
    if _plugin_test_suite is None:
        _plugin_test_suite = PluginTestSuite()
    
    return _plugin_test_suite
