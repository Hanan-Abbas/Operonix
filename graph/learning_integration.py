"""
Learning Integration — Operonix Graph
──────────────────────────────────────

Learning-driven routing integration.
Per migration plan Phase 14: Learning-Driven Routing & Adaptation

This module integrates the existing learning system with the graph's candidate
discovery and ranking to improve routing decisions based on historical performance.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

logger = logging.getLogger("Graph.LearningIntegration")


@dataclass
class PerformanceSignal:
    """Performance signal from execution.
    
    Args:
        task_id: Task identifier
        intent: Intent that was executed
        method_type: Method type that was used
        success: Whether execution succeeded
        execution_time: Execution time in seconds
        retry_count: Number of retries
        fallback_used: Whether fallback was used
        timestamp: Timestamp of execution
    """
    task_id: str
    intent: str
    method_type: str
    success: bool
    execution_time: float
    retry_count: int
    fallback_used: bool
    timestamp: float


class LearningIntegration:
    """Integration layer for learning-driven routing.
    
    This class bridges the graph with the existing learning system to:
    - Collect performance feedback from graph execution
    - Retrieve historical signals for candidate ranking
    - Apply policy-controlled adjustments to candidate scores
    
    Per migration plan Phase 14: Learning-Driven Routing & Adaptation
    """
    
    def __init__(self):
        """Initialize the learning integration."""
        self.learner = None
        self.retriever = None
        self.performance_history: List[PerformanceSignal] = []
        self._initialize_learning_system()
        logger.info("LearningIntegration initialized")
    
    def _initialize_learning_system(self) -> None:
        """Initialize connection to existing learning system."""
        try:
            from learning.learner import PatternLearner
            from learning.retriever import Retriever
            
            # Use existing global instances if available
            try:
                from learning.learner import learner
                self.learner = learner
            except ImportError:
                # Create new instance if global not available
                self.learner = PatternLearner()
            
            try:
                from learning.retriever import retriever
                self.retriever = retriever
            except ImportError:
                self.retriever = Retriever()
            
            logger.info("Connected to existing learning system")
        except ImportError as e:
            logger.warning(f"Could not import learning system: {e}")
    
    def collect_performance_feedback(
        self,
        task_id: str,
        intent: str,
        method_type: str,
        success: bool,
        execution_time: float,
        retry_count: int = 0,
        fallback_used: bool = False
    ) -> None:
        """Collect performance feedback from execution.
        
        Phase 14: Performance feedback collection.
        
        Args:
            task_id: Task identifier
            intent: Intent that was executed
            method_type: Method type that was used
            success: Whether execution succeeded
            execution_time: Execution time in seconds
            retry_count: Number of retries
            fallback_used: Whether fallback was used
        """
        import time
        
        signal = PerformanceSignal(
            task_id=task_id,
            intent=intent,
            method_type=method_type,
            success=success,
            execution_time=execution_time,
            retry_count=retry_count,
            fallback_used=fallback_used,
            timestamp=time.time()
        )
        
        self.performance_history.append(signal)
        
        # Feed to existing learner if available
        if self.learner:
            try:
                # For successful executions, the learner already learns from task_completed events
                # For failed executions, we may want to record routing mismatches
                if not success:
                    self._record_routing_mismatch(intent, method_type)
            except Exception as e:
                logger.error(f"Error feeding performance to learner: {e}")
        
        logger.info(f"Collected performance feedback: {intent} -> {method_type} (success={success})")
    
    def _record_routing_mismatch(self, intent: str, method_type: str) -> None:
        """Record a routing mismatch for learning.
        
        Args:
            intent: Intent that failed
            method_type: Method type that failed
        """
        if self.learner and hasattr(self.learner, '_learn_from_routing_mismatch'):
            try:
                # Simulate routing mismatch event
                event_data = {
                    "intent": intent,
                    "method": method_type,
                    "reason": "execution_failure"
                }
                # The learner's _learn_from_routing_mismatch expects an event with .data attribute
                class MockEvent:
                    def __init__(self, data):
                        self.data = data
                
                self.learner._learn_from_routing_mismatch(MockEvent(event_data))
                logger.info(f"Recorded routing mismatch: {intent} -> {method_type}")
            except Exception as e:
                logger.error(f"Error recording routing mismatch: {e}")
    
    def get_historical_method_ranking(
        self,
        app: str,
        intent: str
    ) -> List[str]:
        """Get historical method ranking for (app, intent) pair.
        
        Phase 14: Historical signals for candidate ranking.
        
        Args:
            app: Application context
            intent: Intent to execute
            
        Returns:
            Ordered list of method types based on historical performance
        """
        if self.retriever:
            try:
                ranking = self.retriever.get_method_ranking(app, intent)
                logger.info(f"Retrieved historical ranking for {app}/{intent}: {ranking}")
                return ranking
            except Exception as e:
                logger.error(f"Error getting historical ranking: {e}")
        
        return []
    
    def apply_learning_adjustment(
        self,
        candidate_score: float,
        intent: str,
        method_type: str,
        app: str = ""
    ) -> float:
        """Apply policy-controlled learning adjustment to candidate score.
        
        Phase 14: Policy-controlled learning interfaces.
        
        This applies a bounded adjustment to the candidate score based on
        historical performance, ensuring learning never bypasses safety or
        deterministic controls.
        
        Args:
            candidate_score: Original candidate score
            intent: Intent to execute
            method_type: Method type being evaluated
            app: Application context (optional)
            
        Returns:
            Adjusted candidate score (bounded to prevent extreme adjustments)
        """
        adjustment = 0.0
        
        # Get historical ranking
        historical_ranking = self.get_historical_method_ranking(app, intent)
        
        # Boost score if method is historically preferred
        if historical_ranking and method_type in historical_ranking:
            # Higher rank = higher boost
            rank_index = historical_ranking.index(method_type)
            rank_boost = (len(historical_ranking) - rank_index) * 0.05  # 5% boost per rank position
            adjustment += rank_boost
            logger.debug(f"Applied historical ranking boost: {rank_boost:.3f} for {method_type}")
        
        # Check for recent performance signals
        recent_signals = [
            s for s in self.performance_history[-100:]  # Last 100 signals
            if s.intent == intent and s.method_type == method_type
        ]
        
        if recent_signals:
            success_rate = sum(1 for s in recent_signals if s.success) / len(recent_signals)
            
            # Boost for high success rate
            if success_rate > 0.8:
                adjustment += 0.1  # 10% boost for >80% success rate
                logger.debug(f"Applied success rate boost: 0.1 for {method_type} (success_rate={success_rate:.2f})")
            # Penalize for low success rate
            elif success_rate < 0.5:
                adjustment -= 0.1  # 10% penalty for <50% success rate
                logger.debug(f"Applied success rate penalty: -0.1 for {method_type} (success_rate={success_rate:.2f})")
        
        # Apply bounded adjustment (max ±20% to prevent extreme changes)
        adjustment = max(-0.2, min(0.2, adjustment))
        
        adjusted_score = candidate_score + adjustment
        
        # Ensure score stays in valid range [0, 1]
        adjusted_score = max(0.0, min(1.0, adjusted_score))
        
        logger.debug(f"Learning adjustment: {candidate_score:.3f} + {adjustment:.3f} = {adjusted_score:.3f}")
        
        return adjusted_score
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get summary of collected performance data.
        
        Args:
            None
            
        Returns:
            Dict with performance summary statistics
        """
        if not self.performance_history:
            return {
                "total_signals": 0,
                "success_rate": 0.0,
                "avg_execution_time": 0.0,
                "by_method": {}
            }
        
        total_signals = len(self.performance_history)
        successful_signals = sum(1 for s in self.performance_history if s.success)
        success_rate = successful_signals / total_signals if total_signals > 0 else 0.0
        
        avg_execution_time = (
            sum(s.execution_time for s in self.performance_history) / total_signals
            if total_signals > 0 else 0.0
        )
        
        # Group by method type
        by_method: Dict[str, Dict[str, Any]] = {}
        for signal in self.performance_history:
            method = signal.method_type
            if method not in by_method:
                by_method[method] = {
                    "total": 0,
                    "successful": 0,
                    "avg_time": 0.0
                }
            
            by_method[method]["total"] += 1
            if signal.success:
                by_method[method]["successful"] += 1
            by_method[method]["avg_time"] += signal.execution_time
        
        # Calculate averages per method
        for method in by_method:
            by_method[method]["success_rate"] = (
                by_method[method]["successful"] / by_method[method]["total"]
            )
            by_method[method]["avg_time"] = (
                by_method[method]["avg_time"] / by_method[method]["total"]
            )
        
        return {
            "total_signals": total_signals,
            "success_rate": success_rate,
            "avg_execution_time": avg_execution_time,
            "by_method": by_method
        }


# Global learning integration instance
_learning_integration: Optional[LearningIntegration] = None


def get_learning_integration() -> LearningIntegration:
    """Get the global learning integration instance.
    
    Returns:
        LearningIntegration instance
    """
    global _learning_integration
    
    if _learning_integration is None:
        _learning_integration = LearningIntegration()
    
    return _learning_integration
