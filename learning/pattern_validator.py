import logging
from core.config import settings


class PatternValidator:
    """🛡️ The Quality Control Inspector of the Learning System.

    Analyzes a successful sequence of steps to ensure it is efficient, safe,
    and doesn't contain infinite loops or redundant operations before it is
    memorized.
    """

    def __init__(self):
        self.logger = logging.getLogger("PatternValidator")

    async def validate_pattern(self, intent: str, steps: list) -> bool:
        """Runs a battery of checks on a plan.

        Returns True if the plan is high-quality and safe to memorize.
        """
        if not steps:
            self.logger.debug(f"Rejected empty plan for intent: {intent}")
            return False

        # Check 1: Redundancy & Loops (Stuttering)
        if self._has_infinite_loops_or_stutters(steps):
            self.logger.warning(
                f"🚨 Rejected pattern for '{intent}': Detected looping or redundant steps."
            )
            return False

        # Check 2: Efficiency (Did it take too many steps for the task type?)
        if self._is_highly_inefficient(intent, steps):
            self.logger.warning(
                f"🚨 Rejected pattern for '{intent}': Plan is too long/inefficient."
            )
            return False

        self.logger.info(
            f"✅ Pattern for '{intent}' passed validation and is safe to learn!"
        )
        return True

    