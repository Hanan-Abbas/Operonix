import logging
import os
import shutil
from datetime import datetime
from core.config import settings


class RollbackManager:
    """♻️ The Safety Net of the AI OS.

    Handles creating, tracking, and restoring file backups to prevent the AI
    from permanently corrupting codebase files.
    """

    def __init__(self):
        self.logger = logging.getLogger("RollbackManager")

    def create_backup(self, file_path: str) -> str:
        """Creates a timestamped backup of a file before it gets edited.

        Returns the path to the backup file.
        """
        try:
            if not os.path.exists(file_path):
                self.logger.warning(
                    f"Cannot create backup. File does not exist: {file_path}"
                )
                return ""

            # Create a unique timestamp (e.g., 20260401_210538)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # We create the backup in the same directory, but you could also
            # route this to a dedicated .backups/ folder if you prefer!
            backup_path = f"{file_path}.bak_{timestamp}"

            shutil.copy(file_path, backup_path)
            self.logger.info(f"💾 Backup successfully created: {backup_path}")
            return backup_path

        except Exception as e:
            self.logger.error(
                f"Failed to create backup for {file_path}. Error: {e}"
            )
            return ""

    def restore_backup(self, file_path: str, backup_path: str) -> bool:
        """Restores a backup over the current file if the AI's fix fails."""
        try:
            if not backup_path or not os.path.exists(backup_path):
                self.logger.warning(
                    f"Cannot restore. Backup file not found: {backup_path}"
                )
                return False

            # Copy the backup over the original file
            shutil.copy(backup_path, file_path)
            self.logger.info(f"♻️ Successfully rolled back {file_path}")

            # Optional: Delete the backup file after successful restore to prevent clutter
            self._delete_backup(backup_path)

            return True

        except Exception as e:
            self.logger.error(
                f"Failed to restore backup {backup_path}. Error: {e}"
            )
            return False

    def _delete_backup(self, backup_path: str):
        """Internal helper to clean up backup files after use."""
        try:
            if os.path.exists(backup_path):
                os.remove(backup_path)
                self.logger.info(
                    f"🧹 Cleaned up temporary backup: {backup_path}"
                )
        except Exception as e:
            self.logger.warning(f"Failed to delete backup file: {e}")


# Global instance to be imported in auto_fix.py or elsewhere
rollback_manager = RollbackManager()