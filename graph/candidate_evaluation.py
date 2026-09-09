"""
Candidate Evaluation Service — Operonix Graph
──────────────────────────────────────────

Candidate evaluation service for execution method selection.
Per migration plan Phase 10: Candidate-Based Routing Engine

The candidate evaluation service evaluates candidates based on:
- capability fit
- context fit
- availability
- reliability
- historical success
- risk
- permissions
- latency
- reversibility
"""
from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional

from migration.domain_contracts import Candidate, CandidateEvaluation, PlanStep, IntentResult, CandidateType

logger = logging.getLogger("Graph.CandidateEvaluation")


class CandidateEvaluationService:
    """Service for evaluating candidate execution methods.
    
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
        """Initialize candidate evaluation service."""
        self.historical_data: Dict[str, Dict[str, Any]] = {}
        
        logger.info("CandidateEvaluationService initialized")
    
    def evaluate_candidates(
        self,
        candidates: List[Candidate],
        plan_step: PlanStep,
        intent: Optional[IntentResult],
        context: Optional[Dict[str, Any]]
    ) -> List[CandidateEvaluation]:
        """Evaluate candidates for a plan step.
        
        Args:
            candidates: List of candidates to evaluate
            plan_step: Current plan step
            intent: Intent result
            context: Current context
            
        Returns:
            List of candidate evaluations
        """
        logger.info(f"Evaluating {len(candidates)} candidates for step: {plan_step.step_id}")
        
        evaluations = []
        
        for candidate in candidates:
            evaluation = self._evaluate_candidate(candidate, plan_step, intent, context)
            evaluations.append(evaluation)
        
        logger.info(f"Evaluated {len(evaluations)} candidates for step {plan_step.step_id}")
        
        return evaluations
    
    def _evaluate_candidate(
        self,
        candidate: Candidate,
        plan_step: PlanStep,
        intent: Optional[IntentResult],
        context: Optional[Dict[str, Any]]
    ) -> CandidateEvaluation:
        """Evaluate a single candidate.
        
        Args:
            candidate: Candidate to evaluate
            plan_step: Current plan step
            intent: Intent result
            context: Current context
            
        Returns:
            Candidate evaluation
        """
        # Evaluate each metric
        candidate.capability_fit = self._evaluate_capability_fit(candidate, plan_step, intent)
        candidate.context_fit = self._evaluate_context_fit(candidate, context)
        candidate.availability = self._evaluate_availability(candidate)
        candidate.reliability = self._evaluate_reliability(candidate)
        candidate.historical_success = self._evaluate_historical_success(candidate)
        candidate.risk = self._evaluate_risk(candidate, plan_step)
        candidate.permissions = self._evaluate_permissions(candidate, context)
        candidate.latency = self._evaluate_latency(candidate)
        candidate.reversibility = self._evaluate_reversibility(candidate, plan_step)
        
        # Calculate overall score
        candidate.overall_score = self._calculate_overall_score(candidate)
        
        # Check constraints
        constraints_satisfied, constraint_violations = self._check_constraints(candidate, plan_step)
        
        evaluation = CandidateEvaluation(
            candidate=candidate,
            evaluation_reason=self._generate_evaluation_reason(candidate, plan_step),
            constraints_satisfied=constraints_satisfied,
            constraint_violations=constraint_violations
        )
        
        return evaluation
    
    def _evaluate_capability_fit(
        self,
        candidate: Candidate,
        plan_step: PlanStep,
        intent: Optional[IntentResult]
    ) -> float:
        """Evaluate how well the candidate's capabilities match the step.
        
        Args:
            candidate: Candidate to evaluate
            plan_step: Current plan step
            intent: Intent result
            
        Returns:
            Capability fit score (0.0 to 1.0)
        """
        step_objective = plan_step.objective.lower()
        
        # Check if candidate type matches the step requirements
        if candidate.candidate_type == CandidateType.SHELL:
            # Shell is good for command execution
            if "command" in step_objective or "execute" in step_objective:
                return 0.9
            elif "file" in step_objective:
                return 0.7
            else:
                return 0.5
        
        elif candidate.candidate_type == CandidateType.API:
            # APIs are good for structured operations
            if "api" in step_objective or "service" in step_objective:
                return 0.9
            elif "data" in step_objective:
                return 0.8
            else:
                return 0.6
        
        elif candidate.candidate_type == CandidateType.PLUGIN:
            # Plugins are good for specialized tasks
            if candidate.metadata.get("capabilities"):
                capabilities = candidate.metadata["capabilities"]
                for cap in capabilities:
                    if cap.lower() in step_objective:
                        return 0.9
            return 0.7
        
        elif candidate.candidate_type == CandidateType.UI:
            # UI automation is good for UI tasks
            if "click" in step_objective or "type" in step_objective or "ui" in step_objective:
                return 0.9
            elif "window" in step_objective or "app" in step_objective:
                return 0.8
            else:
                return 0.4
        
        elif candidate.candidate_type == CandidateType.BROWSER_AUTOMATION:
            # Browser automation is good for web tasks
            if "browser" in step_objective or "web" in step_object or "url" in step_object:
                return 0.9
            elif "page" in step_objective or "site" in step_objective:
                return 0.8
            else:
                return 0.3
        
        elif candidate.candidate_type == CandidateType.VISION:
            # Vision is good for visual tasks
            if "visual" in step_objective or "image" in step_objective or "screenshot" in step_objective:
                return 0.9
            elif "see" in step_objective or "look" in step_objective:
                return 0.7
            else:
                return 0.2
        
        return 0.5
    
    def _evaluate_context_fit(
        self,
        candidate: Candidate,
        context: Optional[Dict[str, Any]]
    ) -> float:
        """Evaluate how well the candidate fits the current context.
        
        Args:
            candidate: Candidate to evaluate
            context: Current context
            
        Returns:
            Context fit score (0.0 to 1.0)
        """
        if not context:
            return 0.5  # Neutral if no context
        
        # UI automation requires window context
        if candidate.candidate_type == CandidateType.UI:
            if context.get("window_title"):
                return 0.9
            else:
                return 0.2
        
        # Browser automation requires browser context
        if candidate.candidate_type == CandidateType.BROWSER_AUTOMATION:
            if context.get("app_type") == "browser":
                return 0.9
            else:
                return 0.2
        
        # Shell works well in terminal context
        if candidate.candidate_type == CandidateType.SHELL:
            if context.get("app_type") == "terminal":
                return 0.8
            else:
                return 0.6
        
        # APIs work well in any context
        if candidate.candidate_type == CandidateType.API:
            return 0.8
        
        # Plugins depend on specific context
        if candidate.candidate_type == CandidateType.PLUGIN:
            if candidate.metadata.get("requires_context"):
                required_context = candidate.metadata["requires_context"]
                if context.get(required_context):
                    return 0.9
                else:
                    return 0.3
            else:
                return 0.7
        
        return 0.5
    
    def _evaluate_availability(self, candidate: Candidate) -> float:
        """Evaluate the availability of the candidate.
        
        Args:
            candidate: Candidate to evaluate
            
        Returns:
            Availability score (0.0 to 1.0)
        """
        # Shell is always available
        if candidate.candidate_type == CandidateType.SHELL:
            return 1.0
        
        # Check if candidate is marked as available
        if candidate.metadata.get("available") is False:
            return 0.0
        
        # APIs may have availability issues
        if candidate.candidate_type == CandidateType.API:
            if candidate.metadata.get("api_status") == "down":
                return 0.0
            elif candidate.metadata.get("api_status") == "degraded":
                return 0.5
            else:
                return 0.9
        
        # Plugins may not be installed
        if candidate.candidate_type == CandidateType.PLUGIN:
            if candidate.metadata.get("installed") is False:
                return 0.0
            else:
                return 0.9
        
        # UI automation requires window
        if candidate.candidate_type == CandidateType.UI:
            if candidate.metadata.get("requires_window"):
                return 0.8  # Availability depends on context
            else:
                return 0.9
        
        # Browser automation requires browser
        if candidate.candidate_type == CandidateType.BROWSER_AUTOMATION:
            if candidate.metadata.get("requires_browser"):
                return 0.8  # Availability depends on context
            else:
                return 0.9
        
        # Vision requires vision system
        if candidate.candidate_type == CandidateType.VISION:
            if candidate.metadata.get("vision_available"):
                return 0.9
            else:
                return 0.0
        
        return 0.8
    
    def _evaluate_reliability(self, candidate: Candidate) -> float:
        """Evaluate the reliability of the candidate.
        
        Args:
            candidate: Candidate to evaluate
            
        Returns:
            Reliability score (0.0 to 1.0)
        """
        # Shell is generally reliable
        if candidate.candidate_type == CandidateType.SHELL:
            return 0.9
        
        # Check metadata for reliability info
        if candidate.metadata.get("reliability"):
            reliability = candidate.metadata["reliability"]
            if isinstance(reliability, (int, float)):
                return min(max(reliability, 0.0), 1.0)
        
        # APIs may have reliability issues
        if candidate.candidate_type == CandidateType.API:
            return 0.8
        
        # Plugins vary in reliability
        if candidate.candidate_type == CandidateType.PLUGIN:
            return 0.7
        
        # UI automation can be flaky
        if candidate.candidate_type == CandidateType.UI:
            return 0.6
        
        # Browser automation can be flaky
        if candidate.candidate_type == CandidateType.BROWSER_AUTOMATION:
            return 0.6
        
        # Vision can be unreliable
        if candidate.candidate_type == CandidateType.VISION:
            return 0.5
        
        return 0.7
    
    def _evaluate_historical_success(self, candidate: Candidate) -> float:
        """Evaluate the historical success rate of the candidate.
        
        Args:
            candidate: Candidate to evaluate
            
        Returns:
            Historical success score (0.0 to 1.0)
        """
        candidate_key = f"{candidate.candidate_type.value}_{candidate.tool_id}"
        
        if candidate_key in self.historical_data:
            historical = self.historical_data[candidate_key]
            success_rate = historical.get("success_rate", 0.5)
            return min(max(success_rate, 0.0), 1.0)
        
        # Default to neutral if no historical data
        return 0.5
    
    def _evaluate_risk(self, candidate: Candidate, plan_step: PlanStep) -> float:
        """Evaluate the risk of using the candidate.
        
        Args:
            candidate: Candidate to evaluate
            plan_step: Current plan step
            
        Returns:
            Risk score (0.0 to 1.0, lower is better)
        """
        # Shell can be risky for destructive operations
        if candidate.candidate_type == CandidateType.SHELL:
            if plan_step.side_effect in ["DESTRUCTIVE", "EXTERNAL_COMMIT"]:
                return 0.3  # High risk
            else:
                return 0.7  # Moderate risk
        
        # APIs are generally low risk
        if candidate.candidate_type == CandidateType.API:
            return 0.9  # Low risk
        
        # Plugins vary in risk
        if candidate.candidate_type == CandidateType.PLUGIN:
            if candidate.metadata.get("trusted"):
                return 0.8
            else:
                return 0.6
        
        # UI automation can be risky
        if candidate.candidate_type == CandidateType.UI:
            return 0.5
        
        # Browser automation can be risky
        if candidate.candidate_type == CandidateType.BROWSER_AUTOMATION:
            return 0.5
        
        # Vision is generally low risk
        if candidate.candidate_type == CandidateType.VISION:
            return 0.8
        
        return 0.7
    
    def _evaluate_permissions(self, candidate: Candidate, context: Optional[Dict[str, Any]]) -> float:
        """Evaluate the permissions of the candidate.
        
        Args:
            candidate: Candidate to evaluate
            context: Current context
            
        Returns:
            Permissions score (0.0 to 1.0)
        """
        # Shell requires execution permissions
        if candidate.candidate_type == CandidateType.SHELL:
            return 0.8
        
        # APIs may require API keys
        if candidate.candidate_type == CandidateType.API:
            if candidate.metadata.get("has_api_key"):
                return 0.9
            else:
                return 0.3
        
        # Plugins may require permissions
        if candidate.candidate_type == CandidateType.PLUGIN:
            if candidate.metadata.get("has_permissions"):
                return 0.9
            else:
                return 0.5
        
        # UI automation requires UI permissions
        if candidate.candidate_type == CandidateType.UI:
            return 0.7
        
        # Browser automation requires browser permissions
        if candidate.candidate_type == CandidateType.BROWSER_AUTOMATION:
            return 0.7
        
        # Vision requires camera/screen permissions
        if candidate.candidate_type == CandidateType.VISION:
            if candidate.metadata.get("has_vision_permissions"):
                return 0.9
            else:
                return 0.3
        
        return 0.7
    
    def _evaluate_latency(self, candidate: Candidate) -> float:
        """Evaluate the latency of the candidate.
        
        Args:
            candidate: Candidate to evaluate
            
        Returns:
            Latency score (0.0 to 1.0, higher is better)
        """
        # Shell is fast
        if candidate.candidate_type == CandidateType.SHELL:
            return 0.9
        
        # APIs can have variable latency
        if candidate.candidate_type == CandidateType.API:
            if candidate.metadata.get("latency") == "low":
                return 0.9
            elif candidate.metadata.get("latency") == "medium":
                return 0.6
            else:
                return 0.3
        
        # Plugins can be fast
        if candidate.candidate_type == CandidateType.PLUGIN:
            return 0.8
        
        # UI automation can be slow
        if candidate.candidate_type == CandidateType.UI:
            return 0.5
        
        # Browser automation can be slow
        if candidate.candidate_type == CandidateType.BROWSER_AUTOMATION:
            return 0.5
        
        # Vision can be slow
        if candidate.candidate_type == CandidateType.VISION:
            return 0.4
        
        return 0.6
    
    def _evaluate_reversibility(self, candidate: Candidate, plan_step: PlanStep) -> float:
        """Evaluate the reversibility of the candidate.
        
        Args:
            candidate: Candidate to evaluate
            plan_step: Current plan step
            
        Returns:
            Reversibility score (0.0 to 1.0, higher is better)
        """
        # Shell operations can be reversible depending on the command
        if计划步骤.idempotency == "IDEMPOTENT":
            return 0.9
        else:
            return 0.4
        
        # APIs may support rollback
        if candidate.candidate_type == CandidateType.API:
            if candidate.metadata.get("supports_rollback"):
                return 0.9
            else:
                return 0.5
        
        # Plugins may support rollback
        if candidate.candidate_type == CandidateType.PLUGIN:
            if candidate.metadata.get("supports_rollback"):
                return 0.9
            else:
                return 0.5
        
        # UI automation is generally not reversible
        if candidate.candidate_type == CandidateType.UI:
            return 0.3
        
        # Browser automation is generally not reversible
        if candidate.candidate_type == CandidateType.BROWSER_AUTOMATION:
            return 0.3
        
        # Vision is read-only, so fully reversible
        if candidate.candidate_type == CandidateType.VISION:
            return 1.0
        
        return 0.5
    
    def _calculate_overall_score(self, candidate: Candidate) -> float:
        """Calculate the overall score for a candidate.
        
        Args:
            candidate: Candidate with individual metric scores
            
        Returns:
            Overall score (0.0 to 1.0)
        """
        # Default weights from RankingPolicy
        weights = {
            "capability_fit": 0.25,
            "context_fit": 0.20,
            "availability": 0.15,
            "reliability": 0.15,
            "historical_success": 0.10,
            "risk": 0.05,
            "permissions": 0.05,
            "latency": 0.03,
            "reversibility": 0.02
        }
        
        # Calculate weighted sum
        overall_score = (
            candidate.capability_fit * weights["capability_fit"] +
            candidate.context_fit * weights["context_fit"] +
            candidate.availability * weights["availability"] +
            candidate.reliability * weights["reliability"] +
            candidate.historical_success * weights["historical_success"] +
            candidate.risk * weights["risk"] +
            candidate.permissions * weights["permissions"] +
            candidate.latency * weights["latency"] +
            candidate.reversibility * weights["reversibility"]
        )
        
        return min(max(overall_score, 0.0), 1.0)
    
    def _check_constraints(
        self,
        candidate: Candidate,
        plan_step: PlanStep
    ) -> tuple[bool, List[str]]:
        """Check if candidate satisfies all constraints.
        
        Args:
            candidate: Candidate to check
            plan_step: Current plan step
            
        Returns:
            Tuple of (constraints_satisfied, constraint_violations)
        """
        constraint_violations = []
        
        # Check availability constraint
        if candidate.availability < 0.5:
            constraint_violations.append("Low availability")
        
        # Check permissions constraint
        if candidate.permissions < 0.5:
            constraint_violations.append("Insufficient permissions")
        
        # Check safety constraint for destructive operations
        if plan_step.side_effect in ["DESTRUCTIVE", "EXTERNAL_COMMIT"]:
            if candidate.candidate_type == CandidateType.SHELL and candidate.risk < 0.5:
                constraint_violations.append("High risk for destructive operation")
        
        constraints_satisfied = len(constraint_violations) == 0
        
        return constraints_satisfied, constraint_violations
    
    def _generate_evaluation_reason(self, candidate: Candidate, plan_step: PlanStep) -> str:
        """Generate a human-readable evaluation reason.
        
        Args:
            candidate: Candidate to evaluate
            plan_step: Current plan step
            
        Returns:
            Evaluation reason string
        """
        reasons = []
        
        if candidate.capability_fit > 0.8:
            reasons.append("high capability fit")
        elif candidate.capability_fit > 0.5:
            reasons.append("moderate capability fit")
        else:
            reasons.append("low capability fit")
        
        if candidate.context_fit > 0.8:
            reasons.append("excellent context fit")
        elif candidate.context_fit > 0.5:
            reasons.append("good context fit")
        else:
            reasons.append("poor context fit")
        
        if candidate.availability < 0.5:
            reasons.append("low availability")
        
        if candidate.risk < 0.5:
            reasons.append("high risk")
        
        return ", ".join(reasons)
    
    def update_historical_data(self, candidate: Candidate, success: bool) -> None:
        """Update historical success data for a candidate.
        
        Args:
            candidate: Candidate that was used
            success: Whether the candidate succeeded
        """
        candidate_key = f"{candidate.candidate_type.value}_{candidate.tool_id}"
        
        if candidate_key not in self.historical_data:
            self.historical_data[candidate_key] = {
                "total_attempts": 0,
                "successful_attempts": 0,
                "success_rate": 0.5
            }
        
        self.historical_data[candidate_key]["total_attempts"] += 1
        if success:
            self.historical_data[candidate_key]["successful_attempts"] += 1
        
        # Calculate success rate
        total = self.historical_data[candidate_key]["total_attempts"]
        successful = self.historical_data[candidate_key]["successful_attempts"]
        self.historical_data[candidate_key]["success_rate"] = successful / total if total > 0 else 0.5
        
        logger.debug(f"Updated historical data for {candidate_key}: success_rate={self.historical_data[candidate_key]['success_rate']}")


# Global candidate evaluation service instance
_candidate_evaluation_service: Optional[CandidateEvaluationService] = None


def get_candidate_evaluation_service() -> CandidateEvaluationService:
    """Get the global candidate evaluation service instance.
    
    Returns:
        CandidateEvaluationService instance
    """
    global _candidate_evaluation_service
    
    if _candidate_evaluation_service is None:
        _candidate_evaluation_service = CandidateEvaluationService()
    
    return _candidate_evaluation_service
