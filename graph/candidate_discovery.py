"""
Candidate Discovery Service — Operonix Graph
──────────────────────────────────────────

Candidate discovery service for execution method selection.
Per migration plan Phase 10: Candidate-Based Routing Engine

The candidate discovery service discovers available execution methods
for a given plan step, including plugins, APIs, shell, UI, browser automation,
vision, remote/local capabilities.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional

from migration.domain_contracts import Candidate, CandidateType, PlanStep, IntentResult
from migration.graph_state import OperonixState

logger = logging.getLogger("Graph.CandidateDiscovery")


class CandidateDiscoveryService:
    """Service for discovering candidate execution methods.
    
    Per migration plan Phase 10: Candidate-Based Routing Engine
    The architecture:
    ```
    PlanStep + Intent + Context
              ↓
    Candidate Discovery
              ↓
    Candidate Evaluation
              ↓
    Policy / Safety Constraints
              ↓
    Ranking
              ↓
    MethodDecision
    ```
    """
    
    def __init__(self):
        """Initialize candidate discovery service."""
        self.available_plugins: Dict[str, Dict[str, Any]] = {}
        self.available_apis: Dict[str, Dict[str, Any]] = {}
        self.available_tools: Dict[str, Dict[str, Any]] = {}
        
        # Phase 12: Integrate with plugin manifest registry
        self._sync_from_plugin_manifest_registry()
        
        logger.info("CandidateDiscoveryService initialized")
    
    def _sync_from_plugin_manifest_registry(self) -> None:
        """Sync available plugins from plugin manifest registry.
        
        Phase 12 enhancement: Automatically register plugins from the plugin manifest registry.
        """
        try:
            from plugins.plugin_manifest import get_plugin_manifest_registry
            
            registry = get_plugin_manifest_registry()
            manifests = registry.get_all_manifests()
            
            for plugin_id, manifest in manifests.items():
                # Convert manifest to plugin info format
                plugin_info = {
                    "plugin_id": manifest.plugin_id,
                    "name": manifest.name,
                    "version": manifest.version,
                    "description": manifest.description,
                    "category": manifest.category.value,
                    "capabilities": [c.capability_id for c in manifest.capabilities],
                    "permissions": [p.value for p in manifest.permissions],
                    "dependencies": manifest.dependencies,
                    "metadata": manifest.metadata
                }
                
                self.available_plugins[plugin_id] = plugin_info
                
            logger.info(f"Synced {len(manifests)} plugins from plugin manifest registry")
        except ImportError:
            logger.warning("Could not import plugin manifest registry")
        except Exception as e:
            logger.error(f"Error syncing from plugin manifest registry: {e}")
    
    def register_plugin(self, plugin_id: str, plugin_info: Dict[str, Any]) -> None:
        """Register a plugin as available.
        
        Args:
            plugin_id: Plugin identifier
            plugin_info: Plugin information (capabilities, metadata, etc.)
        """
        self.available_plugins[plugin_id] = plugin_info
        logger.info(f"Registered plugin: {plugin_id}")
    
    def register_api(self, api_id: str, api_info: Dict[str, Any]) -> None:
        """Register an API as available.
        
        Args:
            api_id: API identifier
            api_info: API information (endpoints, capabilities, metadata, etc.)
        """
        self.available_apis[api_id] = api_info
        logger.info(f"Registered API: {api_id}")
    
    def register_tool(self, tool_id: str, tool_info: Dict[str, Any]) -> None:
        """Register a tool as available.
        
        Args:
            tool_id: Tool identifier
            tool_info: Tool information (type, capabilities, metadata, etc.)
        """
        self.available_tools[tool_id] = tool_info
        logger.info(f"Registered tool: {tool_id}")
    
    def discover_candidates(
        self,
        plan_step: PlanStep,
        intent: Optional[IntentResult],
        context: Optional[Dict[str, Any]]
    ) -> List[Candidate]:
        """Discover candidate execution methods for a plan step.
        
        Args:
            plan_step: Current plan step
            intent: Intent result
            context: Current context
            
        Returns:
            List of candidate execution methods
        """
        logger.info(f"Discovering candidates for step: {plan_step.step_id}")
        
        candidates = []
        
        # Discover shell candidates
        shell_candidates = self._discover_shell_candidates(plan_step, intent, context)
        candidates.extend(shell_candidates)
        
        # Discover API candidates
        api_candidates = self._discover_api_candidates(plan_step, intent, context)
        candidates.extend(api_candidates)
        
        # Discover plugin candidates
        plugin_candidates = self._discover_plugin_candidates(plan_step, intent, context)
        candidates.extend(plugin_candidates)
        
        # Discover UI candidates
        ui_candidates = self._discover_ui_candidates(plan_step, intent, context)
        candidates.extend(ui_candidates)
        
        # Discover browser automation candidates
        browser_candidates = self._discover_browser_candidates(plan_step, intent, context)
        candidates.extend(browser_candidates)
        
        # Discover vision candidates
        vision_candidates = self._discover_vision_candidates(plan_step, intent, context)
        candidates.extend(vision_candidates)
        
        logger.info(f"Discovered {len(candidates)} candidates for step {plan_step.step_id}")
        
        return candidates
    
    def _discover_shell_candidates(
        self,
        plan_step: PlanStep,
        intent: Optional[IntentResult],
        context: Optional[Dict[str, Any]]
    ) -> List[Candidate]:
        """Discover shell execution candidates.
        
        Args:
            plan_step: Current plan step
            intent: Intent result
            context: Current context
            
        Returns:
            List of shell candidates
        """
        candidates = []
        
        # Shell is always available as a fallback
        candidate = Candidate(
            candidate_type=CandidateType.SHELL,
            tool_id="shell",
            capability_id="execute_command",
            metadata={"fallback": True}
        )
        
        candidates.append(candidate)
        
        return candidates
    
    def _discover_api_candidates(
        self,
        plan_step: PlanStep,
        intent: Optional[IntentResult],
        context: Optional[Dict[str, Any]]
    ) -> List[Candidate]:
        """Discover API execution candidates.
        
        Args:
            plan_step: Current plan step
            intent: Intent result
            context: Current context
            
        Returns:
            List of API candidates
        """
        candidates = []
        
        for api_id, api_info in self.available_apis.items():
            # Check if API is relevant to the step
            if self._is_api_relevant(api_info, plan_step, intent):
                candidate = Candidate(
                    candidate_type=CandidateType.API,
                    tool_id=api_id,
                    capability_id=api_info.get("capability_id"),
                    metadata=api_info
                )
                
                candidates.append(candidate)
        
        return candidates
    
    def _discover_plugin_candidates(
        self,
        plan_step: PlanStep,
        intent: Optional[IntentResult],
        context: Optional[Dict[str, Any]]
    ) -> List[Candidate]:
        """Discover plugin execution candidates.
        
        Args:
            plan_step: Current plan step
            intent: Intent result
            context: Current context
            
        Returns:
            List of plugin candidates
        """
        candidates = []
        
        for plugin_id, plugin_info in self.available_plugins.items():
            # Check if plugin is relevant to the step
            if self._is_plugin_relevant(plugin_info, plan_step, intent):
                candidate = Candidate(
                    candidate_type=CandidateType.PLUGIN,
                    tool_id=plugin_id,
                    plugin_id=plugin_id,
                    capability_id=plugin_info.get("capability_id"),
                    metadata=plugin_info
                )
                
                candidates.append(candidate)
        
        return candidates
    
    def _discover_ui_candidates(
        self,
        plan_step: PlanStep,
        intent: Optional[IntentResult],
        context: Optional[Dict[str, Any]]
    ) -> List[Candidate]:
        """Discover UI execution candidates.
        
        Args:
            plan_step: Current plan step
            intent: Intent result
            context: Current context
            
        Returns:
            List of UI candidates
        """
        candidates = []
        
        # UI automation is available if context has window information
        if context and context.get("window_title"):
            candidate = Candidate(
                candidate_type=CandidateType.UI,
                tool_id="ui_automation",
                capability_id="ui_interact",
                metadata={"requires_window": True}
            )
            
            candidates.append(candidate)
        
        return candidates
    
    def _discover_browser_candidates(
        self,
        plan_step: PlanStep,
        intent: Optional[IntentResult],
        context: Optional[Dict[str, Any]]
    ) -> List[Candidate]:
        """Discover browser automation candidates.
        
        Args:
            plan_step: Current plan step
            intent: Intent result
            context: Current context
            
        Returns:
            List of browser automation candidates
        """
        candidates = []
        
        # Browser automation is available if context indicates browser
        if context and context.get("app_type") == "browser":
            candidate = Candidate(
                candidate_type=CandidateType.BROWSER_AUTOMATION,
                tool_id="browser_automation",
                capability_id="browser_interact",
                metadata={"requires_browser": True}
            )
            
            candidates.append(candidate)
        
        return candidates
    
    def _discover_vision_candidates(
        self,
        plan_step: PlanStep,
        intent: Optional[IntentResult],
        context: Optional[Dict[str, Any]]
    ) -> List[Candidate]:
        """Discover vision-based execution candidates.
        
        Args:
            plan_step: Current plan step
            intent: Intent result
            context: Current context
            
        Returns:
            List of vision candidates
        """
        candidates = []
        
        # Vision is available as an option for visual tasks
        if intent and "visual" in intent.name.lower():
            candidate = Candidate(
                candidate_type=CandidateType.VISION,
                tool_id="vision",
                capability_id="visual_recognition",
                metadata={"requires_vision": True}
            )
            
            candidates.append(candidate)
        
        return candidates
    
    def _is_api_relevant(
        self,
        api_info: Dict[str, Any],
        plan_step: PlanStep,
        intent: Optional[IntentResult]
    ) -> bool:
        """Check if an API is relevant to the plan step.
        
        Args:
            api_info: API information
            plan_step: Current plan step
            intent: Intent result
            
        Returns:
            True if API is relevant, False otherwise
        """
        # Check if API capabilities match step objective
        api_capabilities = api_info.get("capabilities", [])
        step_objective = plan_step.objective.lower()
        
        for capability in api_capabilities:
            if capability.lower() in step_objective:
                return True
        
        return False
    
    def _is_plugin_relevant(
        self,
        plugin_info: Dict[str, Any],
        plan_step: PlanStep,
        intent: Optional[IntentResult]
    ) -> bool:
        """Check if a plugin is relevant to the plan step.
        
        Args:
            plugin_info: Plugin information
            plan_step: Current plan step
            intent: Intent result
            
        Returns:
            True if plugin is relevant, False otherwise
        """
        # Check if plugin capabilities match step objective
        plugin_capabilities = plugin_info.get("capabilities", [])
        step_objective = plan_step.objective.lower()
        
        for capability in plugin_capabilities:
            if capability.lower() in step_objective:
                return True
        
        return False


# Global candidate discovery service instance
_candidate_discovery_service: Optional[CandidateDiscoveryService] = None


def get_candidate_discovery_service() -> CandidateDiscoveryService:
    """Get the global candidate discovery service instance.
    
    Returns:
        CandidateDiscoveryService instance
    """
    global _candidate_discovery_service
    
    if _candidate_discovery_service is None:
        _candidate_discovery_service = CandidateDiscoveryService()
    
    return _candidate_discovery_service
