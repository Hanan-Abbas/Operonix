"""
plugins/manifest_schema.py

Defines the canonical schema for plugin manifests.
Every installed plugin has a manifest.json in its directory
that conforms to this schema.

Also defines the BasePlugin interface that all generated plugins must implement.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any


# ── Enums ─────────────────────────────────────────────────────────────────────

class RiskLevel(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"


class PluginStatus(str, Enum):
    PENDING   = "pending"    # Generated, not yet tested
    TESTING   = "testing"    # Currently in sandbox tests
    TRUSTED   = "trusted"    # Passed tests + user approved
    UNTRUSTED = "untrusted"  # Failed tests or revoked
    RETIRED   = "retired"    # Superseded or permanently disabled


# ── Manifest Dataclass ────────────────────────────────────────────────────────

@dataclass
class PluginManifest:
    """
    Canonical schema for a plugin manifest.
    Stored as manifest.json inside each installed/<plugin_name>/ directory.
    """
    # Identity
    name: str
    description: str
    version: str = "1.0"
    author: str = "agent"

    # Capability linkage
    intent: str = ""              # The capability gap intent this plugin fills
    capabilities: list[str] = field(default_factory=list)  # intent strings this plugin handles
    tags: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)

    # Trust & safety
    status: PluginStatus = PluginStatus.PENDING
    risk_level: RiskLevel = RiskLevel.MEDIUM
    trusted: bool = False
    safe_mode: bool = True
    requires_confirmation: bool = False

    # Versioning
    previous_versions: list[str] = field(default_factory=list)
    changelog: list[str] = field(default_factory=list)

    # Performance tracking (updated by plugin_health_monitor)
    total_runs: int = 0
    total_successes: int = 0
    total_failures: int = 0
    last_run_at: str = ""
    last_reviewed: str = ""

    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # Services this plugin is allowed to use (from capability registry)
    allowed_services: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return round(self.total_successes / self.total_runs, 3)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["risk_level"] = self.risk_level.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "PluginManifest":
        data = dict(data)
        if "status" in data:
            data["status"] = PluginStatus(data["status"])
        if "risk_level" in data:
            data["risk_level"] = RiskLevel(data["risk_level"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def save(self, plugin_dir: str):
        """Write manifest.json into the plugin's directory."""
        self.updated_at = datetime.utcnow().isoformat()
        path = os.path.join(plugin_dir, "manifest.json")
        os.makedirs(plugin_dir, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, plugin_dir: str) -> "PluginManifest | None":
        """Load manifest.json from a plugin directory."""
        path = os.path.join(plugin_dir, "manifest.json")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return cls.from_dict(json.load(f))


# ── Base Plugin Interface ─────────────────────────────────────────────────────

class BasePlugin:
    """
    Every generated plugin must subclass BasePlugin.

    Plugins access system capabilities ONLY through the service registry:

        service = registry.get("vision_service")
        if not service or not service.is_available():
            return {"status": "error", "message": "vision_service unavailable"}
        result = await service.run(context, args)

    Direct imports of automation/, context/, or core/ are NOT permitted.
    """

    # ── Class-level metadata (override in subclass) ───────────────────────────
    name: str        = "base_plugin"
    description: str = "Base plugin — do not use directly."
    version: str     = "1.0"
    permissions: list[str] = []
    safe_mode: bool  = True
    allowed_services: list[str] = []

    # ── Interface methods ─────────────────────────────────────────────────────

    async def run(self, context: dict, args: dict) -> dict:
        """
        Main execution entry point.
        Must return a dict: {"status": "success"|"error", ...}
        """
        raise NotImplementedError("Plugin must implement run()")

    def validate(self, args: dict) -> str | None:
        """
        Validate input args before execution.
        Return an error string if invalid, None if OK.
        """
        return None  # Default: no validation

    def is_safe(self, args: dict) -> bool:
        """
        Quick safety pre-check. Override to add custom logic.
        """
        return True

    def get_metadata(self) -> dict:
        """Returns plugin metadata dict for the registry."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "permissions": self.permissions,
            "safe_mode": self.safe_mode,
            "allowed_services": self.allowed_services,
        }


# ── Schema Validation Helper ──────────────────────────────────────────────────

def validate_manifest_dict(data: dict) -> tuple[bool, str]:
    """
    Validates a raw dict against the PluginManifest schema.
    Returns (is_valid, error_message).
    """
    required = ["name", "description", "intent"]
    for field_name in required:
        if not data.get(field_name):
            return False, f"Missing required field: '{field_name}'"

    if "risk_level" in data:
        try:
            RiskLevel(data["risk_level"])
        except ValueError:
            return False, f"Invalid risk_level: {data['risk_level']}"

    if "status" in data:
        try:
            PluginStatus(data["status"])
        except ValueError:
            return False, f"Invalid status: {data['status']}"

    name = data.get("name", "")
    if not name.replace("_", "").replace("-", "").isalnum():
        return False, f"Plugin name must be alphanumeric (with _ or -): '{name}'"

    return True, ""