"""
Plugin Manifest — Operonix Plugin System
──────────────────────────────────────

Plugin manifest format for Operonix plugins.
Per migration plan Phase 12: Plugin Integration

Architecture:
```
Plugin Manifest
      ↓
Capability Descriptor
      ↓
Routing Candidate
      ↓
Safety
      ↓
Executor
      ↓
Plugin
```
"""
from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("Plugins.PluginManifest")


class PluginCategory(str, Enum):
    """Categories of plugins."""
    FILE_OPERATIONS = "file_operations"
    SYSTEM_OPERATIONS = "system_operations"
    NETWORK_OPERATIONS = "network_operations"
    UI_AUTOMATION = "ui_automation"
    DATA_PROCESSING = "data_processing"
    AI_ML = "ai_ml"
    COMMUNICATION = "communication"
    SECURITY = "security"
    CUSTOM = "custom"


class PluginPermission(str, Enum):
    """Permissions required by plugins."""
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_EXECUTE = "file_execute"
    NETWORK_ACCESS = "network_access"
    SYSTEM_ACCESS = "system_access"
    UI_ACCESS = "ui_access"
    CAMERA_ACCESS = "camera_access"
    MICROPHONE_ACCESS = "microphone_access"
    LOCATION_ACCESS = "location_access"
    CUSTOM = "custom"


@dataclass
class CapabilityDescriptor:
    """Descriptor for a plugin capability.
    
    This describes what a plugin can do and how it should be used.
    """
    capability_id: str
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    idempotency: str = "UNKNOWN"  # IDEMPOTENT, NON_IDEMPOTENT, UNKNOWN
    side_effect: str = "UNKNOWN"  # NONE, LOCAL, DESTRUCTIVE, EXTERNAL_COMMIT, UNKNOWN
    reversibility: str = "UNKNOWN"  # REVERSIBLE, NON_REVERSIBLE, UNKNOWN
    permissions: List[PluginPermission] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary.
        
        Returns:
            Dict representation
        """
        return {
            "capability_id": self.capability_id,
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "idempotency": self.idempotency,
            "side_effect": self.side_effect,
            "reversibility": self.reversibility,
            "permissions": [p.value for p in self.permissions],
            "tags": self.tags
        }


@dataclass
class PluginManifest:
    """Manifest for an Operonix plugin.
    
    This describes the plugin, its capabilities, and how it should be integrated
    into the Operonix system.
    
    Per migration plan Phase 12: Plugin Integration
    """
    plugin_id: str
    name: str
    version: str
    description: str
    author: Optional[str] = None
    category: PluginCategory = PluginCategory.CUSTOM
    capabilities: List[CapabilityDescriptor] = field(default_factory=list)
    permissions: List[PluginPermission] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary.
        
        Returns:
            Dict representation
        """
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "category": self.category.value,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "permissions": [p.value for p in self.permissions],
            "dependencies": self.dependencies,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PluginManifest":
        """Create from dictionary.
        
        Args:
            data: Dictionary representation
            
        Returns:
            PluginManifest instance
        """
        capabilities = [
            CapabilityDescriptor(**c) for c in data.get("capabilities", [])
        ]
        
        permissions = [
            PluginPermission(p) for p in data.get("permissions", [])
        ]
        
        return cls(
            plugin_id=data["plugin_id"],
            name=data["name"],
            version=data["version"],
            description=data["description"],
            author=data.get("author"),
            category=PluginCategory(data.get("category", "custom")),
            capabilities=capabilities,
            permissions=permissions,
            dependencies=data.get("dependencies", []),
            metadata=data.get("metadata", {})
        )
    
    def get_capability(self, capability_id: str) -> Optional[CapabilityDescriptor]:
        """Get a capability by ID.
        
        Args:
            capability_id: Capability identifier
            
        Returns:
            CapabilityDescriptor or None if not found
        """
        for capability in self.capabilities:
            if capability.capability_id == capability_id:
                return capability
        return None
    
    def has_capability(self, capability_id: str) -> bool:
        """Check if plugin has a capability.
        
        Args:
            capability_id: Capability identifier
            
        Returns:
            True if capability exists, False otherwise
        """
        return self.get_capability(capability_id) is not None
    
    def requires_permission(self, permission: PluginPermission) -> bool:
        """Check if plugin requires a permission.
        
        Args:
            permission: Permission to check
            
        Returns:
            True if permission is required, False otherwise
        """
        return permission in self.permissions


class PluginManifestRegistry:
    """Registry for plugin manifests.
    
    This registry manages plugin manifests and provides access to them for
    routing and execution.
    """
    
    def __init__(self):
        """Initialize the plugin manifest registry."""
        self.manifests: Dict[str, PluginManifest] = {}
        logger.info("PluginManifestRegistry initialized")
    
    def register_manifest(self, manifest: PluginManifest) -> None:
        """Register a plugin manifest.
        
        Args:
            manifest: Plugin manifest to register
        """
        self.manifests[manifest.plugin_id] = manifest
        logger.info(f"Registered plugin manifest: {manifest.plugin_id}")
    
    def get_manifest(self, plugin_id: str) -> Optional[PluginManifest]:
        """Get a plugin manifest by plugin ID.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            PluginManifest or None if not found
        """
        return self.manifests.get(plugin_id)
    
    def list_manifests(self) -> List[str]:
        """List all registered plugin IDs.
        
        Returns:
            List of plugin IDs
        """
        return list(self.manifests.keys())
    
    def get_all_manifests(self) -> Dict[str, PluginManifest]:
        """Get all registered manifests.
        
        Returns:
            Dict mapping plugin IDs to manifests
        """
        return self.manifests.copy()
    
    def get_manifests_by_category(self, category: PluginCategory) -> List[PluginManifest]:
        """Get manifests by category.
        
        Args:
            category: Plugin category
            
        Returns:
            List of PluginManifest instances
        """
        return [
            manifest for manifest in self.manifests.values()
            if manifest.category == category
        ]
    
    def get_capabilities_for_plugin(self, plugin_id: str) -> List[CapabilityDescriptor]:
        """Get capabilities for a plugin.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            List of CapabilityDescriptor instances
        """
        manifest = self.get_manifest(plugin_id)
        if manifest:
            return manifest.capabilities
        return []
    
    def get_all_capabilities(self) -> Dict[str, List[CapabilityDescriptor]]:
        """Get all capabilities from all plugins.
        
        Returns:
            Dict mapping plugin IDs to capability lists
        """
        capabilities = {}
        for plugin_id, manifest in self.manifests.items():
            capabilities[plugin_id] = manifest.capabilities
        return capabilities


# Global plugin manifest registry instance
_plugin_manifest_registry: Optional[PluginManifestRegistry] = None


def get_plugin_manifest_registry() -> PluginManifestRegistry:
    """Get the global plugin manifest registry instance.
    
    Returns:
        PluginManifestRegistry instance
    """
    global _plugin_manifest_registry
    
    if _plugin_manifest_registry is None:
        _plugin_manifest_registry = PluginManifestRegistry()
    
    return _plugin_manifest_registry
