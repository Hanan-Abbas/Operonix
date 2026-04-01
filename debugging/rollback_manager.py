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

    