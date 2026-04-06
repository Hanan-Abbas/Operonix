import os
import re
from tools.base_tool import BaseTool

class SmartFilePatcher(BaseTool):
    """
    🔧 Universal tool to edit any file using search-and-replace blocks.
    Zero hardcoding. Zero reliance on specific apps or editors.
    """

    def __init__(self):
        super().__init__()
        self.name = "smart_file_patcher"
        self.description = "Edits specific blocks of text in a file without rewriting the whole file."

    def execute(self, file_path: str, search_block: str, replace_block: str) -> dict:
        """
        Searches for a specific block of text in a file and replaces it.
        """
        # 1. Expand paths like '~/Desktop/list.txt' to full absolute paths
        expanded_path = os.path.expanduser(file_path)

        if not os.path.exists(expanded_path):
            return {
                "status": "error",
                "message": f"File not found at {expanded_path}"
            }

        try:
            with open(expanded_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 2. Check if the block to replace actually exists
            if search_block not in content:
                # Let's try to match it ignoring excess whitespace, just in case
                normalized_search = self._normalize_whitespace(search_block)
                normalized_content = self._normalize_whitespace(content)
                
                if normalized_search not in normalized_content:
                    return {
                        "status": "error",
                        "message": "The text you want to replace was not found in the file exactly as described."
                    }
                
                # If it matches normalized, we do a regex replace instead
                pattern = re.escape(search_block).replace(r'\ ', r'\s+')
                new_content = re.sub(pattern, replace_block, content, count=1)
            else:
                # Direct string replacement (super safe and doesn't mess with formatting)
                new_content = content.replace(search_block, replace_block, 1)

            # 3. Save the modified file
            with open(expanded_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return {
                "status": "success",
                "message": f"Successfully updated {os.path.basename(file_path)}!"
            }

        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to patch file: {str(e)}"
            }

    def _normalize_whitespace(self, text: str) -> str:
        """Removes duplicate spaces and newlines for fallback matching."""
        return re.sub(r'\s+', ' ', text).strip()