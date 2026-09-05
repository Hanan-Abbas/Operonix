"""
Baseline Registry — Operonix Migration Phase 0
──────────────────────────────────────────────

Establishes the known-good baseline for regression testing during migration.
This module:
1. Records the current git commit as the baseline
2. Identifies critical workflows to test
3. Provides utilities for baseline comparison
"""
from __future__ import annotations

import subprocess
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger("Baseline")


class BaselineRegistry:
    """Manages the migration baseline information."""
    
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.baseline_file = project_root / "migration" / "baseline.json"
        self.baseline_data = self._load_baseline()
    
    def _load_baseline(self) -> dict[str, Any]:
        """Load existing baseline data or create new."""
        if self.baseline_file.exists():
            import json
            with open(self.baseline_file, 'r') as f:
                return json.load(f)
        return {}
    
    def _save_baseline(self) -> None:
        """Save baseline data to file."""
        import json
        self.baseline_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.baseline_file, 'w') as f:
            json.dump(self.baseline_data, f, indent=2, default=str)
    
    def get_current_git_commit(self) -> Optional[str]:
        """Get the current git commit hash."""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get git commit: {e}")
            return None
    def get_current_git_branch(self) -> Optional[str]:
        """Get the current git branch name."""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get git branch: {e}")
            return None
    
    def establish_baseline(self) -> dict[str, Any]:
        """Establish the current state as the migration baseline."""
        commit = self.get_current_git_commit()
        branch = self.get_current_git_branch()
        
        baseline_info = {
            "baseline_commit": commit,
            "baseline_branch": branch,
            "established_at": datetime.utcnow().isoformat(),
            "phase": "Phase 0: Baseline, Contracts & Safety",
            "status": "established",
            "critical_workflows": self._get_critical_workflows(),
        }
        
        self.baseline_data.update(baseline_info)
        self._save_baseline()
        
        logger.info(f"Baseline established at commit {commit} on branch {branch}")
        return baseline_info
    
    def get_baseline(self) -> Dict[str, Any]:
        """Get the current baseline information."""
        return self.baseline_data
    
    def is_baseline_established(self) -> bool:
        """Check if a baseline has been established."""
        return bool(self.baseline_data.get("baseline_commit"))
    
    def _get_critical_workflows(self) -> list[Dict[str, str]]:
        """Return list of critical workflows for baseline testing.
        
        Per migration plan §7, representative baseline workflows should cover:
        - simple file operation
        - application opening
        - shell operation
        - UI operation
        - web operation
        - multi-step workflow
        - failure + retry
        - failure + fallback/re-route
        - safety rejection
        - confirmation-required action
        """
        return [
            {
                "name": "simple_file_operation",
                "description": "Create, read, or delete a file",
                "priority": "high"
            },
            {
                "name": "application_opening",
                "description": "Open an application (e.g., Firefox)",
                "priority": "high"
            },
            {
                "name": "shell_operation",
                "description": "Execute a shell command",
                "priority": "high"
            },
            {
                "name": "ui_operation",
                "description": "Perform UI automation (click, type)",
                "priority": "high"
            },
            {
                "name": "web_operation",
                "description": "Perform web browser automation",
                "priority": "medium"
            },
            {
                "name": "multi_step_workflow",
                "description": "Open Firefox and search for autonomous agents",
                "priority": "high"
            },
            {
                "name": "failure_retry",
                "description": "Handle transient failure with retry",
                "priority": "high"
            },
            {
                "name": "failure_fallback",
                "description": "Handle failure with fallback/re-route",
                "priority": "high"
            },
            {
                "name": "safety_rejection",
                "description": "Reject unsafe operation",
                "priority": "high"
            },
            {
                "name": "confirmation_required",
                "description": "Require user confirmation for risky action",
                "priority": "high"
            },
        ]


def get_baseline_registry() -> BaselineRegistry:
    """Get the baseline registry instance."""
    project_root = Path(__file__).resolve().parent.parent
    return BaselineRegistry(project_root)


def establish_migration_baseline() -> Dict[str, Any]:
    """Establish the migration baseline.
    
    This should be called at the start of Phase 0 to record the
    known-good state before any migration changes.
    """
    registry = get_baseline_registry()
    return registry.establish_baseline()
