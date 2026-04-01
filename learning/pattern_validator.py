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

    def _has_infinite_loops_or_stutters(self, steps: list) -> bool:
        """Checks if the AI got stuck in a loop or repeated the same action

        multiple times before succeeding.
        """
        for i in range(len(steps) - 2):
            # Check for direct repetition: Action A -> Action A -> Action A
            if (
                steps[i].get("action") == steps[i + 1].get("action")
                and steps[i].get("action") == steps[i + 2].get("action")
            ):
                return True

            # Check for ping-pong loops: Action A -> Action B -> Action A
            if (
                steps[i].get("action") == steps[i + 2].get("action")
                and steps[i].get("action") != steps[i + 1].get("action")
            ):
                return True

        return False

    def _is_highly_inefficient(self, intent: str, steps: list) -> bool:
        """Checks if the plan used an unreasonable amount of steps for simple

        tasks.
        """
        step_count = len(steps)

        # File operations are usually direct APIs and shouldn't take many steps
        if "file" in intent and step_count > 4:
            return True

        # Fallback limit for any complex pattern (You can add this to settings later!)
        max_allowed_steps = getattr(settings, "MAX_PATTERN_STEPS", 12)
        if step_count > max_allowed_steps:
            return True

        return False


# Global instance
pattern_validator = PatternValidator()