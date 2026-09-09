"""
Ranking Policy Service — Operonix Graph
──────────────────────────────────────

Ranking policy service for execution method selection.
Per migration plan Phase 10: Candidate-Based Routing Engine

The ranking policy service ranks candidates based on:
- capability fit
- context fit
- availability
- reliability
- historical success
- risk
- permissions
- latency
- reversibility

And applies policy/safety constraints.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional

from migration.domain_contracts import Candidate, CandidateEvaluation, RankingPolicy, RoutingDecision, PlanStep

logger = logging.getLogger("Graph.RankingPolicy")


class RankingPolicyService:
    """Service for ranking candidates and making routing decisions.
    
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
        """Initialize ranking policy service."""
        self.default_policy = RankingPolicy(
            policy_name="default",
            weights={
                "capability_fit": 0.25,
                "context_fit": 0.20,
                "availability": 0.15,
                "reliability": 0.15,
                "historical_success": 0.10,
                "risk": 0.05,
                "permissions": 0.05,
                "latency": 0.03,
                "reversibility": 0.02
            },
            min_threshold=0.5,
            require_all_constraints=True
        )
        
        logger.info("RankingPolicyService initialized")
    
    def rank_candidates(
        self,
        evaluations: List[CandidateEvaluation],
        policy: Optional[RankingPolicy] = None
    ) -> List[CandidateEvaluation]:
        """Rank candidates based on policy.
        
        Args:
            evaluations: List of candidate evaluations
            policy: Ranking policy to use (default if None)
            
        Returns:
            Ranked list of candidate evaluations
        """
        if policy is None:
            policy = self.default_policy
        
        logger.info(f"Ranking {len(evaluations)} candidates with policy: {policy.policy_name}")
        
        # Filter candidates that don't satisfy constraints
        if policy.require_all_constraints:
            valid_evaluations = [e for e in evaluations if e.constraints_satisfied]
        else:
            valid_evaluations = evaluations
        
        logger.info(f"Valid candidates after constraint check: {len(valid_evaluations)}")
        
        # Recalculate scores with policy weights
        for evaluation in valid_evaluations:
            evaluation.candidate.overall_score = self._calculate_weighted_score(
                evaluation.candidate,
                policy.weights
            )
        
        # Sort by overall score (descending)
        ranked_evaluations = sorted(
            valid_evaluations,
            key=lambda e: e.candidate.overall_score,
            reverse=True
        )
        
        logger.info(f"Ranked candidates: {[(e.candidate.candidate_type.value, e.candidate.overall_score) for e in ranked_evaluations]}")
        
        return ranked_evaluations
    
    def make_routing_decision(
        self,
        ranked_evaluations: List[CandidateEvaluation],
        policy: Optional[RankingPolicy] = None
    ) -> Optional[RoutingDecision]:
        """Make routing decision from ranked candidates.
        
        Args:
            ranked_evaluations: Ranked list of candidate evaluations
            policy: Ranking policy to use (default if None)
            
        Returns:
            Routing decision or None if no valid candidates
        """
        if policy is None:
            policy = self.default_policy
        
        if not ranked_evaluations:
            logger.warning("No valid candidates to make routing decision")
            return None
        
        # Select top candidate
        top_evaluation = ranked_evaluations[0]
        top_candidate = top_evaluation.candidate
        
        # Check if top candidate meets minimum threshold
        if top_candidate.overall_score < policy.min_threshold:
            logger.warning(f"Top candidate score {top_candidate.overall_score} below threshold {policy.min_threshold}")
            return None
        
        # Create routing decision
        decision = RoutingDecision(
            selected_candidate=top_candidate,
            candidates_considered=[e.candidate for e in ranked_evaluations],
            confidence=top_candidate.overall_score,
            routing_explanation=self._generate_routing_explanation(ranked_evaluations, policy),
            ranking_policy_id=policy.policy_id
        )
        
        logger.info(f"Routing decision: {top_candidate.candidate_type.value} (score: {top_candidate.overall_score})")
        
        return decision
    
    def _calculate_weighted_score(
        self,
        candidate: Candidate,
        weights: Dict[str, float]
    ) -> float:
        """Calculate weighted score for a candidate.
        
        Args:
            candidate: Candidate with individual metric scores
            weights: Policy weights
            
        Returns:
            Weighted score (0.0 to 1.0)
        """
        weighted_score = (
            candidate.capability_fit * weights.get("capability_fit", 0.25) +
            candidate.context_fit * weights.get("context_fit", 0.20) +
            candidate.availability * weights.get("availability", 0.15) +
            candidate.reliability * weights.get("reliability", 0.15) +
            candidate.historical_success * weights.get("historical_success", 0.10) +
            candidate.risk * weights.get("risk", 0.05) +
            candidate.permissions * weights.get("permissions", 0.05) +
            candidate.latency * weights.get("latency", 0.03) +
            candidate.reversibility * weights.get("reversibility", 0.02)
        )
        
        return min(max(weighted_score, 0.0), 1.0)
    
    def _generate_routing_explanation(
        self,
        ranked_evaluations: List[CandidateEvaluation],
        policy: RankingPolicy
    ) -> str:
        """Generate human-readable routing explanation.
        
        Args:
            ranked_evaluations: Ranked list of candidate evaluations
            policy: Ranking policy used
            
        Returns:
            Routing explanation string
        """
        if not ranked_evaluations:
            return "No valid candidates available"
        
        top_candidate = ranked_evaluations[0].candidate
        top_score = top_candidate.overall_score
        
        explanation_parts = [
            f"Selected {top_candidate.candidate_type.value} with score {top_score:.2f}"
        ]
        
        # Add key factors
        factors = []
        if top_candidate.capability_fit > 0.8:
            factors.append(f"high capability fit ({top_candidate.capability_fit:.2f})")
        if top_candidate.context_fit > 0.8:
            factors.append(f"excellent context fit ({top_candidate.context_fit:.2f})")
        if top_candidate.availability > 0.8:
            factors.append(f"high availability ({top_candidate.availability:.2f})")
        if top_candidate.reliability > 0.8:
            factors.append(f"high reliability ({top_candidate.reliability:.2f})")
        
        if factors:
            explanation_parts.append(f"due to {', '.join(factors)}")
        
        # Add policy info
        explanation_parts.append(f"using policy '{policy.policy_name}'")
        
        # Add alternatives considered
        if len(ranked_evaluations) > 1:
            alternatives = ranked_evaluations[1:3]  # Top 2 alternatives
            alt_info = [f"{e.candidate.candidate_type.value} ({e.candidate.overall_score:.2f})" for e in alternatives]
            explanation_parts.append(f"alternatives: {', '.join(alt_info)}")
        
        return ". ".join(explanation_parts)
    
    def create_custom_policy(
        self,
        policy_name: str,
        weights: Optional[Dict[str, float]] = None,
        min_threshold: float = 0.5,
        require_all_constraints: bool = True
    ) -> RankingPolicy:
        """Create a custom ranking policy.
        
        Args:
            policy_name: Name of the policy
            weights: Custom weights (default if None)
            min_threshold: Minimum score threshold
            require_all_constraints: Whether to require all constraints
            
        Returns:
            Ranking policy
        """
        if weights is None:
            weights = self.default_policy.weights
        
        policy = RankingPolicy(
            policy_name=policy_name,
            weights=weights,
            min_threshold=min_threshold,
            require_all_constraints=require_all_constraints
        )
        
        logger.info(f"Created custom policy: {policy_name}")
        
        return policy
    
    def get_default_policy(self) -> RankingPolicy:
        """Get the default ranking policy.
        
        Returns:
            Default ranking policy
        """
        return self.default_policy


# Global ranking policy service instance
_ranking_policy_service: Optional[RankingPolicyService] = None


def get_ranking_policy_service() -> RankingPolicyService:
    """Get the global ranking policy service instance.
    
    Returns:
        RankingPolicyService instance
    """
    global _ranking_policy_service
    
    if _ranking_policy_service is None:
        _ranking_policy_service = RankingPolicyService()
    
    return _ranking_policy_service
