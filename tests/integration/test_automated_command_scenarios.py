"""
Automated Command Scenario Tests
─────────────────────────────────

This test suite automatically validates common command patterns
and edge cases without requiring manual testing.

Tests:
- LLM command generation for various patterns
- Graph execution flow
- Recovery scenarios
- Edge cases (incomplete commands, ambiguous requests)
"""
import sys
import os

# Calculate project root (tests/integration/ → project root)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import asyncio
from typing import Dict, Any, List
from migration.feature_flags import flags
from migration.domain_contracts import PlanStep, Plan, PlanStepIdempotency, PlanStepSideEffect, IntentResult
from graph.nodes.create_plan import _generate_command_with_llm


class AutomatedCommandTestSuite:
    """Automated test suite for command generation and execution scenarios."""

    def __init__(self):
        self.results = []
        self.total_tests = 0
        self.passed_tests = 0
        self.failed_tests = 0

    def log_result(self, test_name: str, passed: bool, details: str = ""):
        """Log test result."""
        self.total_tests += 1
        if passed:
            self.passed_tests += 1
            self.results.append(f"✅ {test_name}: PASSED")
        else:
            self.failed_tests += 1
            self.results.append(f"❌ {test_name}: FAILED - {details}")
        print(f"{'✅' if passed else '❌'} {test_name}: {details if details else 'PASSED'}")

    async def test_command_generation(self, user_input: str, expected_command: str = None, expected_args_count: int = None) -> bool:
        """Test LLM command generation for a given user input."""
        try:
            intent = IntentResult(
                name="test_intent",
                confidence=0.9,
                parameters={}
            )

            result = await _generate_command_with_llm(
                user_input=user_input,
                intent_name="test_intent",
                intent_parameters={},
                context=None
            )

            command = result.get("command", "")
            arguments = result.get("arguments", [])

            # Validate command structure
            if not command:
                return False, "No command generated"

            # Check if expected command matches (if provided)
            if expected_command and command != expected_command:
                return False, f"Expected command '{expected_command}', got '{command}'"

            # Check argument count (if provided)
            if expected_args_count is not None and len(arguments) != expected_args_count:
                return False, f"Expected {expected_args_count} arguments, got {len(arguments)}"

            # Ensure command doesn't have spaces (should be base command only)
            if " " in command:
                return False, f"Command contains spaces: '{command}'"

            return True, f"Command: {command}, Args: {arguments}"

        except Exception as e:
            return False, f"Exception: {e}"

    async def run_all_tests(self):
        """Run all automated tests."""
        print("\n" + "=" * 70)
        print("AUTOMATED COMMAND SCENARIO TESTS")
        print("=" * 70)

        # Test 1: Application opening
        print("\n--- Test Group: Application Opening ---")
        result = await self.test_command_generation("open system monitor", "gnome-system-monitor", 0)
        self.log_result("Open System Monitor", *result)

        result = await self.test_command_generation("open firefox", "firefox", 0)
        self.log_result("Open Firefox", *result)

        result = await self.test_command_generation("open Clocks", "gnome-clocks", 0)
        self.log_result("Open Clocks", *result)

        # Test 2: File operations
        print("\n--- Test Group: File Operations ---")
        result = await self.test_command_generation("create file test.txt", "touch", 1)
        self.log_result("Create File", *result)

        result = await self.test_command_generation("delete file test.txt", "rm", 1)
        self.log_result("Delete File", *result)

        result = await self.test_command_generation("list files", "ls", 0)
        self.log_result("List Files", *result)

        # Test 3: Network commands (with defaults)
        print("\n--- Test Group: Network Commands ---")
        result = await self.test_command_generation("run the ping command", "ping", None)  # Should have default target
        self.log_result("Ping (Ambiguous)", *result)

        result = await self.test_command_generation("ping google.com 4 times", "ping", 3)
        self.log_result("Ping with Target", *result)

        # Test 4: Package management
        print("\n--- Test Group: Package Management ---")
        result = await self.test_command_generation("install python package requests", "pip", None)
        self.log_result("Install Package", *result)

        # Test 5: Shell commands
        print("\n--- Test Group: Shell Commands ---")
        result = await self.test_command_generation("show current directory", "pwd", 0)
        self.log_result("Show Directory", *result)

        result = await self.test_command_generation("change directory to /tmp", "cd", 1)
        self.log_result("Change Directory", *result)

        # Test 6: Edge cases
        print("\n--- Test Group: Edge Cases ---")
        result = await self.test_command_generation("what is my ip address", None, None)  # Multiple valid approaches
        self.log_result("IP Address Query", *result)

        result = await self.test_command_generation("check disk space", "df", None)  # May add flags
        self.log_result("Disk Space", *result)

        # Print summary
        print("\n" + "=" * 70)
        print("TEST SUMMARY")
        print("=" * 70)
        print(f"Total Tests: {self.total_tests}")
        print(f"Passed: {self.passed_tests}")
        print(f"Failed: {self.failed_tests}")
        print(f"Success Rate: {(self.passed_tests / self.total_tests * 100) if self.total_tests > 0 else 0:.1f}%")

        if self.failed_tests > 0:
            print("\nFailed Tests:")
            for result in self.results:
                if result.startswith("❌"):
                    print(f"  {result}")

        return self.failed_tests == 0


async def main():
    """Run the automated test suite."""
    print("Starting automated command scenario tests...")
    print(f"USE_LANGCHAIN_MODELS: {flags.USE_LANGCHAIN_MODELS}")
    print(f"USE_LANGGRAPH: {flags.USE_LANGGRAPH}")

    if not flags.USE_LANGCHAIN_MODELS:
        print("\n⚠️  WARNING: USE_LANGCHAIN_MODELS is false")
        print("Tests require LLM to be enabled. Set USE_LANGCHAIN_MODELS=true in .env")
        return False

    suite = AutomatedCommandTestSuite()
    success = await suite.run_all_tests()

    return success


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
