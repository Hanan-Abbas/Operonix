"""
Plugin Integration Tests — Operonix Migration Phase 12
──────────────────────────────────────────────────────

Tests for plugin integration.
Per migration plan Phase 12: Plugin Integration
"""
from __future__ import annotations

import pytest


# ─── PLUGIN MANIFEST TESTS ───────────────────────────────────────────────────

def test_plugin_category_enum():
    """Test that PluginCategory enum has all required values."""
    from plugins.plugin_manifest import PluginCategory
    
    assert PluginCategory.FILE_OPERATIONS.value == "file_operations"
    assert PluginCategory.SYSTEM_OPERATIONS.value == "system_operations"
    assert PluginCategory.NETWORK_OPERATIONS.value == "network_operations"
    assert PluginCategory.UI_AUTOMATION.value == "ui_automation"
    assert PluginCategory.DATA_PROCESSING.value == "data_processing"
    assert PluginCategory.AI_ML.value == "ai_ml"
    assert PluginCategory.COMMUNICATION.value == "communication"
    assert PluginCategory.SECURITY.value == "security"
    assert PluginCategory.CUSTOM.value == "custom"


def test_plugin_permission_enum():
    """Test that PluginPermission enum has all required values."""
    from plugins.plugin_manifest import PluginPermission
    
    assert PluginPermission.FILE_READ.value == "file_read"
    assert PluginPermission.FILE_WRITE.value == "file_write"
    assert PluginPermission.FILE_EXECUTE.value == "file_execute"
    assert PluginPermission.NETWORK_ACCESS.value == "network_access"
    assert PluginPermission.SYSTEM_ACCESS.value == "system_access"
    assert PluginPermission.UI_ACCESS.value == "ui_access"
    assert PluginPermission.CAMERA_ACCESS.value == "camera_access"
    assert PluginPermission.MICROPHONE_ACCESS.value == "microphone_access"
    assert PluginPermission.LOCATION_ACCESS.value == "location_access"
    assert PluginPermission.CUSTOM.value == "custom"


def test_capability_descriptor():
    """Test that CapabilityDescriptor can be created."""
    from plugins.plugin_manifest import CapabilityDescriptor, PluginPermission
    
    descriptor = CapabilityDescriptor(
        capability_id="test_capability",
        name="Test Capability",
        description="A test capability",
        permissions=[PluginPermission.FILE_READ]
    )
    
    assert descriptor.capability_id == "test_capability"
    assert descriptor.name == "Test Capability"
    assert descriptor.permissions == [PluginPermission.FILE_READ]


def test_capability_descriptor_to_dict():
    """Test that CapabilityDescriptor can be converted to dict."""
    from plugins.plugin_manifest import CapabilityDescriptor, PluginPermission
    
    descriptor = CapabilityDescriptor(
        capability_id="test_capability",
        name="Test Capability",
        description="A test capability",
        permissions=[PluginPermission.FILE_READ]
    )
    
    data = descriptor.to_dict()
    
    assert data["capability_id"] == "test_capability"
    assert data["name"] == "Test Capability"
    assert data["permissions"] == ["file_read"]


def test_plugin_manifest():
    """Test that PluginManifest can be created."""
    from plugins.plugin_manifest import PluginManifest, PluginCategory, CapabilityDescriptor, PluginPermission
    
    capability = CapabilityDescriptor(
        capability_id="test_capability",
        name="Test Capability",
        description="A test capability"
    )
    
    manifest = PluginManifest(
        plugin_id="test_plugin",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        category=PluginCategory.CUSTOM,
        capabilities=[capability],
        permissions=[PluginPermission.FILE_READ]
    )
    
    assert manifest.plugin_id == "test_plugin"
    assert manifest.name == "Test Plugin"
    assert manifest.version == "1.0.0"
    assert manifest.category == PluginCategory.CUSTOM
    assert len(manifest.capabilities) == 1
    assert len(manifest.permissions) == 1


def test_plugin_manifest_to_dict():
    """Test that PluginManifest can be converted to dict."""
    from plugins.plugin_manifest import PluginManifest, PluginCategory, CapabilityDescriptor, PluginPermission
    
    capability = CapabilityDescriptor(
        capability_id="test_capability",
        name="Test Capability",
        description="A test capability"
    )
    
    manifest = PluginManifest(
        plugin_id="test_plugin",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        category=PluginCategory.CUSTOM,
        capabilities=[capability],
        permissions=[PluginPermission.FILE_READ]
    )
    
    data = manifest.to_dict()
    
    assert data["plugin_id"] == "test_plugin"
    assert data["name"] == "Test Plugin"
    assert data["category"] == "custom"
    assert len(data["capabilities"]) == 1
    assert data["permissions"] == ["file_read"]


def test_plugin_manifest_from_dict():
    """Test that PluginManifest can be created from dict."""
    from plugins.plugin_manifest import PluginManifest, PluginCategory
    
    data = {
        "plugin_id": "test_plugin",
        "name": "Test Plugin",
        "version": "1.0.0",
        "description": "A test plugin",
        "category": "custom",
        "capabilities": [
            {
                "capability_id": "test_capability",
                "name": "Test Capability",
                "description": "A test capability",
                "input_schema": {},
                "output_schema": {},
                "idempotency": "UNKNOWN",
                "side_effect": "UNKNOWN",
                "reversibility": "UNKNOWN",
                "permissions": [],
                "tags": []
            }
        ],
        "permissions": ["file_read"],
        "dependencies": [],
        "metadata": {}
    }
    
    manifest = PluginManifest.from_dict(data)
    
    assert manifest.plugin_id == "test_plugin"
    assert manifest.name == "Test Plugin"
    assert manifest.category == PluginCategory.CUSTOM
    assert len(manifest.capabilities) == 1
    assert len(manifest.permissions) == 1


def test_plugin_manifest_get_capability():
    """Test that PluginManifest can get a capability by ID."""
    from plugins.plugin_manifest import PluginManifest, CapabilityDescriptor
    
    capability1 = CapabilityDescriptor(
        capability_id="capability1",
        name="Capability 1",
        description="First capability"
    )
    
    capability2 = CapabilityDescriptor(
        capability_id="capability2",
        name="Capability 2",
        description="Second capability"
    )
    
    manifest = PluginManifest(
        plugin_id="test_plugin",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        capabilities=[capability1, capability2]
    )
    
    retrieved = manifest.get_capability("capability1")
    
    assert retrieved == capability1
    assert retrieved.capability_id == "capability1"


def test_plugin_manifest_has_capability():
    """Test that PluginManifest can check if it has a capability."""
    from plugins.plugin_manifest import PluginManifest, CapabilityDescriptor
    
    capability = CapabilityDescriptor(
        capability_id="test_capability",
        name="Test Capability",
        description="A test capability"
    )
    
    manifest = PluginManifest(
        plugin_id="test_plugin",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        capabilities=[capability]
    )
    
    assert manifest.has_capability("test_capability") is True
    assert manifest.has_capability("non_existent") is False


def test_plugin_manifest_requires_permission():
    """Test that PluginManifest can check if it requires a permission."""
    from plugins.plugin_manifest import PluginManifest, PluginPermission
    
    manifest = PluginManifest(
        plugin_id="test_plugin",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        permissions=[PluginPermission.FILE_READ]
    )
    
    assert manifest.requires_permission(PluginPermission.FILE_READ) is True
    assert manifest.requires_permission(PluginPermission.FILE_WRITE) is False


# ─── PLUGIN MANIFEST REGISTRY TESTS ───────────────────────────────────────────

def test_plugin_manifest_registry_initialization():
    """Test that PluginManifestRegistry can be initialized."""
    from plugins.plugin_manifest import PluginManifestRegistry
    
    registry = PluginManifestRegistry()
    
    assert registry is not None
    assert len(registry.manifests) == 0


def test_plugin_manifest_registry_register_manifest():
    """Test that manifests can be registered."""
    from plugins.plugin_manifest import PluginManifestRegistry, PluginManifest, PluginCategory
    
    manifest = PluginManifest(
        plugin_id="test_plugin",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        category=PluginCategory.CUSTOM
    )
    
    registry = PluginManifestRegistry()
    registry.register_manifest(manifest)
    
    assert "test_plugin" in registry.manifests
    assert registry.manifests["test_plugin"] == manifest


def test_plugin_manifest_registry_get_manifest():
    """Test that manifests can be retrieved."""
    from plugins.plugin_manifest import PluginManifestRegistry, PluginManifest, PluginCategory
    
    manifest = PluginManifest(
        plugin_id="test_plugin",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        category=PluginCategory.CUSTOM
    )
    
    registry = PluginManifestRegistry()
    registry.register_manifest(manifest)
    
    retrieved = registry.get_manifest("test_plugin")
    
    assert retrieved == manifest


def test_plugin_manifest_registry_get_manifest_not_found():
    """Test that get_manifest returns None for non-existent manifest."""
    from plugins.plugin_manifest import PluginManifestRegistry
    
    registry = PluginManifestRegistry()
    
    retrieved = registry.get_manifest("non_existent")
    
    assert retrieved is None


def test_plugin_manifest_registry_list_manifests():
    """Test that list_manifests returns all plugin IDs."""
    from plugins.plugin_manifest import PluginManifestRegistry, PluginManifest, PluginCategory
    
    registry = PluginManifestRegistry()
    
    manifest1 = PluginManifest(
        plugin_id="plugin1",
        name="Plugin 1",
        version="1.0.0",
        description="Plugin 1",
        category=PluginCategory.CUSTOM
    )
    
    manifest2 = PluginManifest(
        plugin_id="plugin2",
        name="Plugin 2",
        version="1.0.0",
        description="Plugin 2",
        category=PluginCategory.CUSTOM
    )
    
    registry.register_manifest(manifest1)
    registry.register_manifest(manifest2)
    
    plugin_ids = registry.list_manifests()
    
    assert "plugin1" in plugin_ids
    assert "plugin2" in plugin_ids
    assert len(plugin_ids) == 2


def test_plugin_manifest_registry_get_all_manifests():
    """Test that get_all_manifests returns all manifests."""
    from plugins.plugin_manifest import PluginManifestRegistry, PluginManifest, PluginCategory
    
    registry = PluginManifestRegistry()
    
    manifest1 = PluginManifest(
        plugin_id="plugin1",
        name="Plugin 1",
        version="1.0.0",
        description="Plugin 1",
        category=PluginCategory.CUSTOM
    )
    
    manifest2 = PluginManifest(
        plugin_id="plugin2",
        name="Plugin 2",
        version="1.0.0",
        description="Plugin 2",
        category=PluginCategory.CUSTOM
    )
    
    registry.register_manifest(manifest1)
    registry.register_manifest(manifest2)
    
    manifests = registry.get_all_manifests()
    
    assert "plugin1" in manifests
    assert "plugin2" in manifests
    assert len(manifests) == 2


def test_plugin_manifest_registry_get_manifests_by_category():
    """Test that get_manifests_by_category filters by category."""
    from plugins.plugin_manifest import PluginManifestRegistry, PluginManifest, PluginCategory
    
    registry = PluginManifestRegistry()
    
    manifest1 = PluginManifest(
        plugin_id="plugin1",
        name="Plugin 1",
        version="1.0.0",
        description="Plugin 1",
        category=PluginCategory.FILE_OPERATIONS
    )
    
    manifest2 = PluginManifest(
        plugin_id="plugin2",
        name="Plugin 2",
        version="1.0.0",
        description="Plugin 2",
        category=PluginCategory.CUSTOM
    )
    
    registry.register_manifest(manifest1)
    registry.register_manifest(manifest2)
    
    file_ops = registry.get_manifests_by_category(PluginCategory.FILE_OPERATIONS)
    
    assert len(file_ops) == 1
    assert file_ops[0].plugin_id == "plugin1"


def test_plugin_manifest_registry_get_capabilities_for_plugin():
    """Test that get_capabilities_for_plugin returns capabilities."""
    from plugins.plugin_manifest import PluginManifestRegistry, PluginManifest, PluginCategory, CapabilityDescriptor
    
    registry = PluginManifestRegistry()
    
    capability = CapabilityDescriptor(
        capability_id="test_capability",
        name="Test Capability",
        description="A test capability"
    )
    
    manifest = PluginManifest(
        plugin_id="test_plugin",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        category=PluginCategory.CUSTOM,
        capabilities=[capability]
    )
    
    registry.register_manifest(manifest)
    
    capabilities = registry.get_capabilities_for_plugin("test_plugin")
    
    assert len(capabilities) == 1
    assert capabilities[0].capability_id == "test_capability"


def test_plugin_manifest_registry_get_all_capabilities():
    """Test that get_all_capabilities returns all capabilities."""
    from plugins.plugin_manifest import PluginManifestRegistry, PluginManifest, PluginCategory, CapabilityDescriptor
    
    registry = PluginManifestRegistry()
    
    capability1 = CapabilityDescriptor(
        capability_id="capability1",
        name="Capability 1",
        description="First capability"
    )
    
    capability2 = CapabilityDescriptor(
        capability_id="capability2",
        name="Capability 2",
        description="Second capability"
    )
    
    manifest1 = PluginManifest(
        plugin_id="plugin1",
        name="Plugin 1",
        version="1.0.0",
        description="Plugin 1",
        category=PluginCategory.CUSTOM,
        capabilities=[capability1]
    )
    
    manifest2 = PluginManifest(
        plugin_id="plugin2",
        name="Plugin 2",
        version="1.0.0",
        description="Plugin 2",
        category=PluginCategory.CUSTOM,
        capabilities=[capability2]
    )
    
    registry.register_manifest(manifest1)
    registry.register_manifest(manifest2)
    
    all_capabilities = registry.get_all_capabilities()
    
    assert "plugin1" in all_capabilities
    assert "plugin2" in all_capabilities
    assert len(all_capabilities["plugin1"]) == 1
    assert len(all_capabilities["plugin2"]) == 1


# ─── CANDIDATE DISCOVERY PLUGIN INTEGRATION TESTS ───────────────────────────

def test_candidate_discovery_syncs_from_plugin_manifest():
    """Test that CandidateDiscoveryService syncs from plugin manifest registry."""
    from graph.candidate_discovery import CandidateDiscoveryService
    from plugins.plugin_manifest import PluginManifest, PluginCategory, CapabilityDescriptor, get_plugin_manifest_registry
    
    # Register a manifest
    capability = CapabilityDescriptor(
        capability_id="test_capability",
        name="Test Capability",
        description="A test capability"
    )
    
    manifest = PluginManifest(
        plugin_id="test_plugin",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        category=PluginCategory.CUSTOM,
        capabilities=[capability]
    )
    
    registry = get_plugin_manifest_registry()
    registry.register_manifest(manifest)
    
    # Create discovery service (should sync from registry)
    service = CandidateDiscoveryService()
    
    # Check if plugin was registered
    assert "test_plugin" in service.available_plugins


# ─── PLUGIN ADAPTER PERMISSION CHECKING TESTS ───────────────────────────────

def test_plugin_adapter_checks_permissions():
    """Test that PluginAdapter checks permissions from manifest."""
    from graph.tool_adapter import PluginAdapter
    from plugins.plugin_manifest import PluginManifest, PluginCategory, PluginPermission
    
    class MockPlugin:
        description = "Mock plugin"
        parameters = {}
        
        def execute(self, **kwargs):
            return {"result": "success"}
    
    manifest = PluginManifest(
        plugin_id="test_plugin",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        category=PluginCategory.CUSTOM,
        permissions=[PluginPermission.FILE_READ]
    )
    
    plugin = MockPlugin()
    adapter = PluginAdapter("test_plugin", "test_capability", plugin, manifest)
    
    # Should check permissions (currently always returns True)
    assert adapter._check_permissions() is True


def test_plugin_adapter_without_manifest():
    """Test that PluginAdapter works without manifest."""
    from graph.tool_adapter import PluginAdapter
    
    class MockPlugin:
        description = "Mock plugin"
        parameters = {}
        
        def execute(self, **kwargs):
            return {"result": "success"}
    
    plugin = MockPlugin()
    adapter = PluginAdapter("test_plugin", "test_capability", plugin)
    
    # Should work without manifest
    assert adapter._check_permissions() is True


# ─── PLUGIN TESTING FRAMEWORK TESTS ───────────────────────────────────────────

def test_plugin_tester_initialization():
    """Test that PluginTester can be initialized."""
    from plugins.plugin_testing import PluginTester
    
    class MockPlugin:
        pass
    
    plugin = MockPlugin()
    tester = PluginTester(plugin)
    
    assert tester.plugin == plugin
    assert tester.test_cases == []


def test_plugin_tester_add_test_case():
    """Test that test cases can be added."""
    from plugins.plugin_testing import PluginTester, TestCase, TestResult
    
    class MockPlugin:
        pass
    
    def test_function(plugin):
        return {"success": True}
    
    plugin = MockPlugin()
    tester = PluginTester(plugin)
    
    test_case = TestCase(
        name="test_case",
        description="Test case",
        test_function=test_function,
        expected_result={"success": True}
    )
    
    tester.add_test_case(test_case)
    
    assert len(tester.test_cases) == 1
    assert tester.test_cases[0] == test_case


def test_plugin_tester_run_test():
    """Test that a test case can be run."""
    from plugins.plugin_testing import PluginTester, TestCase, TestResult
    
    class MockPlugin:
        def execute(self, **kwargs):
            return {"success": True}
    
    def test_function(plugin, **kwargs):
        return plugin.execute(**kwargs)
    
    plugin = MockPlugin()
    tester = PluginTester(plugin)
    
    test_case = TestCase(
        name="test_case",
        description="Test case",
        test_function=test_function,
        expected_result={"success": True}
    )
    
    execution = tester.run_test(test_case)
    
    assert execution.result == TestResult.PASSED


def test_plugin_tester_run_all_tests():
    """Test that all test cases can be run."""
    from plugins.plugin_testing import PluginTester, TestCase, TestResult
    
    class MockPlugin:
        def execute(self, **kwargs):
            return {"success": True}
    
    def test_function(plugin, **kwargs):
        return plugin.execute(**kwargs)
    
    plugin = MockPlugin()
    tester = PluginTester(plugin)
    
    test_case1 = TestCase(
        name="test_case1",
        description="Test case 1",
        test_function=test_function,
        expected_result={"success": True}
    )
    
    test_case2 = TestCase(
        name="test_case2",
        description="Test case 2",
        test_function=test_function,
        expected_result={"success": True}
    )
    
    tester.add_test_case(test_case1)
    tester.add_test_case(test_case2)
    
    executions = tester.run_all_tests()
    
    assert len(executions) == 2
    assert all(e.result == TestResult.PASSED for e in executions)


def test_plugin_tester_get_test_summary():
    """Test that test summary can be generated."""
    from plugins.plugin_testing import PluginTester, TestCase, TestResult
    
    class MockPlugin:
        def execute(self, **kwargs):
            return {"success": True}
    
    def test_function(plugin, **kwargs):
        return plugin.execute(**kwargs)
    
    plugin = MockPlugin()
    tester = PluginTester(plugin)
    
    test_case = TestCase(
        name="test_case",
        description="Test case",
        test_function=test_function,
        expected_result={"success": True}
    )
    
    tester.add_test_case(test_case)
    executions = tester.run_all_tests()
    summary = tester.get_test_summary(executions)
    
    assert summary["total"] == 1
    assert summary["passed"] == 1
    assert summary["failed"] == 0
    assert summary["success_rate"] == 1.0


def test_plugin_tester_validate_manifest():
    """Test that plugin manifest can be validated."""
    from plugins.plugin_testing import PluginTester
    from plugins.plugin_manifest import PluginManifest, PluginCategory, CapabilityDescriptor
    
    capability = CapabilityDescriptor(
        capability_id="test_capability",
        name="Test Capability",
        description="A test capability"
    )
    
    manifest = PluginManifest(
        plugin_id="test_plugin",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        category=PluginCategory.CUSTOM,
        capabilities=[capability]
    )
    
    plugin = None
    tester = PluginTester(plugin, manifest)
    
    validation = tester.validate_manifest()
    
    assert validation["valid"] is True
    assert len(validation["errors"]) == 0


def test_plugin_tester_validate_manifest_missing_fields():
    """Test that manifest validation detects missing fields."""
    from plugins.plugin_testing import PluginTester
    from plugins.plugin_manifest import PluginManifest, PluginCategory
    
    manifest = PluginManifest(
        plugin_id="",  # Missing
        name="",  # Missing
        version="",  # Missing
        description="",  # Missing
        category=PluginCategory.CUSTOM
    )
    
    plugin = None
    tester = PluginTester(plugin, manifest)
    
    validation = tester.validate_manifest()
    
    assert validation["valid"] is False
    assert len(validation["errors"]) > 0


def test_plugin_tester_test_capability():
    """Test that a capability can be tested."""
    from plugins.plugin_testing import PluginTester, TestResult
    
    class MockPlugin:
        def execute(self, **kwargs):
            return {"success": True}
    
    plugin = MockPlugin()
    tester = PluginTester(plugin)
    
    execution = tester.test_capability("test_capability", {"param": "value"})
    
    assert execution.result == TestResult.PASSED


def test_plugin_test_suite_initialization():
    """Test that PluginTestSuite can be initialized."""
    from plugins.plugin_testing import PluginTestSuite
    
    suite = PluginTestSuite()
    
    assert suite is not None
    assert len(suite.testers) == 0


def test_plugin_test_suite_add_plugin():
    """Test that plugins can be added to the test suite."""
    from plugins.plugin_testing import PluginTestSuite
    
    class MockPlugin:
        pass
    
    plugin = MockPlugin()
    suite = PluginTestSuite()
    
    tester = suite.add_plugin("test_plugin", plugin)
    
    assert "test_plugin" in suite.testers
    assert tester.plugin == plugin


def test_plugin_test_suite_run_all_tests():
    """Test that all tests can be run for all plugins."""
    from plugins.plugin_testing import PluginTestSuite
    
    class MockPlugin:
        def execute(self, **kwargs):
            return {"success": True}
    
    plugin1 = MockPlugin()
    plugin2 = MockPlugin()
    
    suite = PluginTestSuite()
    suite.add_plugin("plugin1", plugin1)
    suite.add_plugin("plugin2", plugin2)
    
    results = suite.run_all_tests()
    
    assert "plugin1" in results
    assert "plugin2" in results


def test_plugin_test_suite_get_aggregated_summary():
    """Test that aggregated summary can be generated."""
    from plugins.plugin_testing import PluginTestSuite
    
    class MockPlugin:
        def execute(self, **kwargs):
            return {"success": True}
    
    plugin = MockPlugin()
    suite = PluginTestSuite()
    suite.add_plugin("test_plugin", plugin)
    
    results = suite.run_all_tests()
    summary = suite.get_aggregated_summary(results)
    
    assert summary["total_plugins"] == 1
    assert "total_tests" in summary
    assert "overall_success_rate" in summary


# ─── GLOBAL INSTANCES TESTS ───────────────────────────────────────────────────

def test_get_plugin_manifest_registry():
    """Test that get_plugin_manifest_registry returns singleton."""
    from plugins.plugin_manifest import get_plugin_manifest_registry
    
    registry1 = get_plugin_manifest_registry()
    registry2 = get_plugin_manifest_registry()
    
    assert registry1 is registry2


def test_get_plugin_test_suite():
    """Test that get_plugin_test_suite returns singleton."""
    from plugins.plugin_testing import get_plugin_test_suite
    
    suite1 = get_plugin_test_suite()
    suite2 = get_plugin_test_suite()
    
    assert suite1 is suite2
