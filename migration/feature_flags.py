"""
Feature Flags System — Operonix Migration
─────────────────────────────────────────

Provides a centralized feature flag system for gradual migration rollout.
All migration-related toggles are defined here with safe defaults.

Usage:
    from migration.feature_flags import flags
    
    if flags.USE_LANGGRAPH:
        # Use new graph-based workflow
        pass
    else:
        # Use legacy event-driven workflow
        pass
"""
from __future__ import annotations

import os
import logging
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=True)

logger = logging.getLogger("FeatureFlags")


class FeatureFlags:
    """Centralized feature flag configuration for Operonix migration.
    
    All flags default to False to ensure safe, opt-in migration.
    Flags can be overridden via environment variables or .env file.
    """
    
    # ─── GRAPH WORKFLOW FLAGS ────────────────────────────────────────────────
    
    USE_LANGGRAPH: bool = os.getenv("USE_LANGGRAPH", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Enable LangGraph-based task workflow (Phase 1+)"""
    
    USE_LANGCHAIN_MODELS: bool = os.getenv("USE_LANGCHAIN_MODELS", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Enable LangChain model adapter for AI operations (Phase 2+)"""
    
    # ─── GRAPH COMPONENT FLAGS ────────────────────────────────────────────────
    
    USE_GRAPH_ROUTING: bool = os.getenv("USE_GRAPH_ROUTING", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Enable candidate-based routing engine in graph (Phase 10+)"""
    
    USE_GRAPH_EXECUTION: bool = os.getenv("USE_GRAPH_EXECUTION", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Enable graph-based execution orchestration (Phase 4+)"""
    
    USE_GRAPH_CONFIRMATION: bool = os.getenv("USE_GRAPH_CONFIRMATION", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Enable graph-based confirmation flow with pause/resume (Phase 7+)"""
    
    # ─── RELIABILITY FLAGS ────────────────────────────────────────────────────
    
    USE_VERIFICATION: bool = os.getenv("USE_VERIFICATION", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Enable post-execution verification (Phase 5+)"""
    
    USE_RECOVERY: bool = os.getenv("USE_RECOVERY", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Enable state-aware recovery routing (Phase 5+)"""
    
    USE_CHECKPOINTING: bool = os.getenv("USE_CHECKPOINTING", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Enable workflow checkpointing and resume (Phase 7+)"""
    
    USE_IDEMPOTENCY_CHECKS: bool = os.getenv("USE_IDEMPOTENCY_CHECKS", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Enable idempotency and side-effect safety checks (Phase 6+)"""
    
    # ─── ADVANCED FEATURE FLAGS ───────────────────────────────────────────────
    
    USE_CANDIDATE_ROUTING: bool = os.getenv("USE_CANDIDATE_ROUTING", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Enable candidate-based routing instead of fixed priority (Phase 10+)"""
    
    USE_TOOL_ADAPTERS: bool = os.getenv("USE_TOOL_ADAPTERS", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Enable LangChain tool adapters (Phase 11+)"""
    
    USE_RAG_MEMORY: bool = os.getenv("USE_RAG_MEMORY", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Enable RAG and memory integration in graph (Phase 13+)"""
    
    USE_LEARNING_ROUTING: bool = os.getenv("USE_LEARNING_ROUTING", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Enable learning-driven routing adaptation (Phase 14+)"""
    
    # ─── MIGRATION CONTROL FLAGS ──────────────────────────────────────────────
    
    MIGRATION_SHADOW_MODE: bool = os.getenv("MIGRATION_SHADOW_MODE", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Run graph in shadow mode alongside legacy for comparison (no execution)"""
    
    MIGRATION_DRY_RUN: bool = os.getenv("MIGRATION_DRY_RUN", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Enable dry-run mode for testing without actual execution"""
    
    # ─── SAFETY FLAGS ─────────────────────────────────────────────────────────
    
    SAFETY_STRICT_MODE: bool = os.getenv("SAFETY_STRICT_MODE", "true").lower() in (
        "1", "true", "yes", "on"
    )
    """Enable strict safety mode (always on by default for safety)"""
    
    SAFETY_ALLOW_BYPASS: bool = os.getenv("SAFETY_ALLOW_BYPASS", "false").lower() in (
        "1", "true", "yes", "on"
    )
    """Allow safety bypass (DANGEROUS - only for testing)"""
    
    def __init__(self) -> None:
        """Initialize feature flags and log current state."""
        self._log_flags()
    
    def _log_flags(self) -> None:
        """Log all feature flag states for observability."""
        logger.info("Feature Flags State:")
        for attr_name in dir(self):
            if attr_name.startswith("_") or callable(getattr(self, attr_name)):
                continue
            if attr_name.isupper():
                value = getattr(self, attr_name)
                logger.info(f"  {attr_name}: {value}")
    
    def get_all_flags(self) -> dict[str, bool]:
        """Return all feature flags as a dictionary."""
        return {
            name: getattr(self, name)
            for name in dir(self)
            if name.isupper() and not callable(getattr(self, name))
        }
    
    def is_migration_active(self) -> bool:
        """Check if any migration feature is active."""
        return any([
            self.USE_LANGGRAPH,
            self.USE_LANGCHAIN_MODELS,
            self.USE_GRAPH_ROUTING,
            self.USE_GRAPH_EXECUTION,
        ])
    
    def get_migration_phase(self) -> str:
        """Determine current migration phase based on active flags."""
        if not self.is_migration_active():
            return "Phase 0: Baseline"
        
        if self.USE_LEARNING_ROUTING:
            return "Phase 14+: Learning-Driven Adaptation"
        if self.USE_RAG_MEMORY:
            return "Phase 13+: RAG & Memory Integration"
        if self.USE_TOOL_ADAPTERS:
            return "Phase 11+: Tool Adapters"
        if self.USE_CANDIDATE_ROUTING:
            return "Phase 10+: Candidate-Based Routing"
        if self.USE_CHECKPOINTING:
            return "Phase 7+: Checkpointing & Human Intervention"
        if self.USE_IDEMPOTENCY_CHECKS:
            return "Phase 6+: Idempotency & Side-Effect Safety"
        if self.USE_RECOVERY or self.USE_VERIFICATION:
            return "Phase 5+: Verification & Recovery"
        if self.USE_LANGGRAPH and self.USE_LANGCHAIN_MODELS:
            return "Phase 4+: First Vertical Slice"
        if self.USE_LANGCHAIN_MODELS:
            return "Phase 2-3: AI Integration"
        if self.USE_LANGGRAPH:
            return "Phase 1: Graph Foundation"
        
        return "Unknown Phase"


# Global singleton instance
flags = FeatureFlags()
