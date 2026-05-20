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
        self.restricted_actions = {"format_disk", "run_shell"}

        # Services that require elevated OS permissions or are outright blocked
        # in the current environment.  Checked by context_builder before
        # provisioning any service for a plugin.
        self.restricted_services: set[str] = set()          # blocked entirely
        self.admin_required_services: set[str] = {          # need root/admin
            "process_bridge",
        }

    # -------------------------
    # Service Permission Check  ← NEW
    # -------------------------
    def check_service_access(self, service: str) -> tuple[bool, str]:
        """
        Returns (True, "Allowed") if the service can be provisioned in the
        current OS environment.
        Returns (False, reason) if the service is restricted or requires
        admin privileges the agent doesn't have.

        Called by context_builder.build() for every service in
        manifest.allowed_services after permission_guard.check_services().
        """
        if service in self.restricted_services:
            return False, f"Service '{service}' is restricted in this environment."

        if service in self.admin_required_services:
            if not self._is_admin():
                return (
                    False,
                    f"Service '{service}' requires admin/root privileges "
                    f"which the agent does not currently have.",
                )

        return True, "Service access allowed."

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

    def is_actually_writable(self, path: str) -> bool:
        """Checks if the OS will physically let the agent write to this path.
        Prevents crashes before the executor tries to touch the disk.
        """
        # If the file doesn't exist yet, check its parent directory
        target = path if os.path.exists(path) else os.path.dirname(path)

        # os.W_OK checks for real OS write permissions
        return os.access(target, os.W_OK)
        
# -------------------------
# Global Instance
# -------------------------
permission_checker = PermissionChecker()