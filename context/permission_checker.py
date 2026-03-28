import os
import platform
import logging

logger = logging.getLogger("PermissionChecker")

class PermissionChecker:
    """
    Checks if the current environment, user, or context allows
    performing a specific action safely.
    """

    def __init__(self):
        self.os_name = platform.system()
        self.restricted_paths = ["/etc", "/bin", "/usr/bin"]  # UNIX system paths
        self.restricted_actions = {"delete_file", "format_disk", "run_shell"}

    # -------------------------
    # Action Permission Check
    # -------------------------
    def is_action_allowed(self, action: str, target_path: str = None) -> (bool, str):
        """
        Returns (True, "Allowed") if action can proceed
        Returns (False, reason) if action is unsafe
        """
        # Restricted action check
        if action in self.restricted_actions:
            return False, f"Action '{action}' is restricted!"

        # Path-based restriction
        if target_path:
            normalized_path = os.path.normpath(target_path)
            for restricted in self.restricted_paths:
                if normalized_path.startswith(restricted):
                    return False, f"Path '{normalized_path}' is protected"

        # Admin/root requirement
        if action in {"install_package", "system_update"}:
            if not self._is_admin():
                return False, f"Admin privileges required for '{action}'"

        return True, "Action Allowed"

    # -------------------------
    # Internal: Check Admin
    # -------------------------
    def _is_admin(self):
        try:
            if self.os_name == "Windows":
                import ctypes
                return ctypes.windll.shell32.IsUserAnAdmin() != 0
            else:
                return os.geteuid() == 0
        except Exception as e:
            logger.warning(f"Failed to check admin privileges: {e}")
            return False

    # -------------------------
    # Utility: Safe Path Check
    # -------------------------
    def is_path_safe(self, path: str) -> bool:
        normalized_path = os.path.normpath(path)
        for restricted in self.restricted_paths:
            if normalized_path.startswith(restricted):
                return False
        return True


# -------------------------
# Global Instance
# -------------------------
permission_checker = PermissionChecker()
