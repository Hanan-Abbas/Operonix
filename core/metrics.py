from dataclasses import dataclass, field
from typing import Dict
import time

@dataclass
class SystemMetrics:
    """Track agent performance metrics."""
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    
    total_duration_seconds: float = 0.0
    task_count_by_intent: Dict[str, int] = field(default_factory=dict)
    
    stt_attempts: int = 0
    stt_confidence_sum: float = 0.0
    
    uptime_seconds: float = field(default_factory=lambda: time.time())
    
    def avg_task_duration(self) -> float:
        """Average task duration in seconds."""
        if self.total_tasks == 0:
            return 0.0
        return self.total_duration_seconds / self.total_tasks
    
    def success_rate(self) -> float:
        """Task success rate (0-100)."""
        if self.total_tasks == 0:
            return 0.0
        return (self.successful_tasks / self.total_tasks) * 100
    
    def avg_stt_confidence(self) -> float:
        """Average STT confidence (0-1)."""
        if self.stt_attempts == 0:
            return 0.0
        return self.stt_confidence_sum / self.stt_attempts

metrics = SystemMetrics()