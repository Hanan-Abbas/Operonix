import logging
import re
from enum import Enum

logger = logging.getLogger("RiskRules")


class RiskLevel(Enum):
    """Defines the severity of an intercepted action."""

    SAFE = 0  # No danger detected
    LOW = 1  # Minor risk, can proceed automatically
    HIGH = 2  # Requires explicit human confirmation
    FORBIDDEN = 3  # Blocked automatically (highly destructive)


# =========================================================
# 🔍 BEHAVIORAL ATTACK PATTERNS (REGEX)
# =========================================================

# 1. Obfuscation & Encoded Payloads
# Attackers hide malicious code in Base64 or hex to bypass static filters.
OBFUSCATION_PATTERN = re.compile(
    r"(base64|decode|eval|exec|\bhex\b|charcode|\\x[0-9a-fA-F]{2})",
    re.IGNORECASE,
)

# 2. Directory Traversal
# Prevents the AI from backing out of safe folders to touch the root OS.
TRAVERSAL_PATTERN = re.compile(r"(\.\./|\.\.\\)")

# 3. Pipe-to-Shell & Remote Execution
# Prevents downloading a script and running it immediately without saving it.
PIPE_TO_SHELL_PATTERN = re.compile(
    r"(curl|wget|fetch).*?\|.*?(sh|bash|python|php|ruby|nc|ncat)",
    re.IGNORECASE,
)

# 4. Destructive Flags
# Looks for force/recursive deletions or disk formatting patterns.
