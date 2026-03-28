# context/context_validator.py
import logging
from core.event_bus import bus
from context.permission_checker import permission_checker

class ContextValidator:
    """
    🛡️ Validates if the current environment/context is safe
    for executing a given intent.
    Integrates system-level permission checks via PermissionChecker.
    """

    def __init__(self):
        self.logger = logging.getLogger("ContextValidator")

    async def validate_action_context(self, intent: str, current_context: dict):
        """
        Validate if the current environment is suitable for the intent.
        Returns: (bool, str) -> (is_valid, reason_message)
        """
        active_app = current_context.get("active_window", "").lower()
        app_type = current_context.get("app_type")
        state = current_context.get("state", {})

        self.logger.debug(f"Validating context for intent: '{intent}' | App: {active_app} ({app_type})")

        # -----------------------------
        # 1️⃣ Permission Checker Integration
        # -----------------------------
        target_path = state.get("target_path")
        allowed, reason = permission_checker.is_action_allowed(intent, target_path)
        if not allowed:
            self.logger.warning(f"PermissionChecker blocked intent '{intent}': {reason}")
            return False, reason

        # -----------------------------
        # 2️⃣ File Operation Safety
        # -----------------------------
        if "file" in intent:
            # extra safety: prevent modifying system dirs without admin
            if target_path and ("/etc/" in target_path or "/usr/" in target_path):
                if not state.get("is_admin", False):
                    msg = f"Blocked: Insufficient permissions to modify {target_path}"
                    self.logger.warning(msg)
                    return False, msg

        # -----------------------------
        # 3️⃣ UI Operation Safety
        # -----------------------------
        if intent in ["click_ui", "type_text", "write_code"]:
            if app_type not in ["editor", "terminal"]:
                msg = f"Context mismatch: Cannot perform '{intent}' in app type '{app_type}'"
                self.logger.warning(msg)
                return False, msg

        # -----------------------------
        # 4️⃣ Web / Browser Safety
        # -----------------------------
        if app_type == "browser":
            domain = state.get("current_url_domain", "")
            if any(d in domain for d in ["bank", "finance", "payment"]):
                msg = f"Security block: Automation disabled on sensitive site '{domain}'"
                self.logger.warning(msg)
                return False, msg

        # -----------------------------
        # 5️⃣ Shell / Dangerous Commands
        # -----------------------------
        dangerous_intents = ["run_shell", "delete_file", "install_package"]
        if intent in dangerous_intents:
            if not state.get("is_admin", False):
                msg = f"Blocked: '{intent}' requires admin privileges"
                self.logger.warning(msg)
                return False, msg

        # -----------------------------
        # ✅ All checks passed
        # -----------------------------
        self.logger.info(f"Context validated for intent '{intent}' in app '{active_app}'")
        return True, "Context Validated"


# -----------------------------
# Global instance
# -----------------------------
context_validator = ContextValidator()
