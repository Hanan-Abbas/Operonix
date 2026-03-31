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
DESTRUCTIVE_FLAGS_PATTERN = re.compile(
    r"(\s-(r|f|rf|fr|R)\b|mkfs|dd\s+if=|>\s*/dev/|wipe|shred)", re.IGNORECASE
)

# 5. Sensitive File Patterns
# Dynamically flags attempts to touch crypto keys, env files, or shell histories.
SENSITIVE_FILES_PATTERN = re.compile(
    r"(\.env|\.ssh|id_rsa|passwd|shadow|bash_history|zsh_history|master\.key)",
    re.IGNORECASE,
)


# =========================================================
# 🛡️ DYNAMIC RISK ASSESSMENT ENGINES
# =========================================================


def get_command_risk(command: str) -> RiskLevel:
    """Analyzes a raw shell command string based on behavior and patterns."""
    if not command:
        return RiskLevel.SAFE

    trimmed_cmd = command.strip()

    # 🛑 1. Check for Pipe-to-Shell (Forbidden)
    if PIPE_TO_SHELL_PATTERN.search(trimmed_cmd):
        logger.warning(f"🚨 FORBIDDEN: Pipe-to-shell detected in '{trimmed_cmd}'")
        return RiskLevel.FORBIDDEN

    # 🛑 2. Check for Destructive Flags/Commands on Root (Forbidden)
    if DESTRUCTIVE_FLAGS_PATTERN.search(trimmed_cmd):
        # If it's targeting root or system directories, it's strictly forbidden
        if re.search(r"(\s/|\sC:\\)", trimmed_cmd):
            logger.warning(
                f"🚨 FORBIDDEN: Destructive command on root detected: '{trimmed_cmd}'"
            )
            return RiskLevel.FORBIDDEN
        # Otherwise, if it's just a regular deletion, it's high risk (needs confirmation)
        return RiskLevel.HIGH

    # ⚠️ 3. Check for Obfuscation (High Risk)
    if OBFUSCATION_PATTERN.search(trimmed_cmd):
        logger.info(
            f"⚠️ HIGH RISK: Obfuscated/Encoded payload detected: '{trimmed_cmd}'"
        )
        return RiskLevel.HIGH

    # ⚠️ 4. Check for Directory Traversal in the command
    if TRAVERSAL_PATTERN.search(trimmed_cmd):
        logger.info(
            f"⚠️ HIGH RISK: Directory traversal in command: '{trimmed_cmd}'"
        )
        return RiskLevel.HIGH

    return RiskLevel.SAFE


def get_file_op_risk(intent: str, file_path: str) -> RiskLevel:
    """Analyzes risks involving files by examining path structures."""
    if not file_path:
        return RiskLevel.SAFE

    path_lower = file_path.lower()

    # 🛑 1. Check for Directory Traversal (Forbidden)
    if TRAVERSAL_PATTERN.search(path_lower):
        logger.warning(
            f"🚨 FORBIDDEN: Directory traversal blocked in path: {file_path}"
        )
        return RiskLevel.FORBIDDEN

    # 🛑 2. Check for operations on absolute root or drive letters (Forbidden)
    if path_lower.strip() in ["/", "c:", "c:\\", "/root"]:
        if intent in ["delete_file", "write_file", "move_file"]:
            logger.warning(
                f"🚨 FORBIDDEN: Attempt to modify root directly: {file_path}"
            )
            return RiskLevel.FORBIDDEN

    # ⚠️ 3. Check for Sensitive Files (High Risk)
    if SENSITIVE_FILES_PATTERN.search(path_lower):
        logger.info(f"⚠️ HIGH RISK: Accessing sensitive file: {file_path}")
        return RiskLevel.HIGH

    return RiskLevel.SAFE


def get_web_op_risk(url: str) -> RiskLevel:
    """Analyzes risks involving web navigation without hardcoding domains."""
    if not url:
        return RiskLevel.SAFE

    url_lower = url.lower()

    # 🛑 1. Check for local network probing (Forbidden)
    # Prevents the AI from accessing your local router or local network devices
    local_network_patterns = re.compile(
        r"(localhost|127\.0\.0\.1|192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)"
    )
    if local_network_patterns.search(url_lower):
        logger.warning(
            f"🚨 FORBIDDEN: Attempt to access local network: {url}"
        )
        return RiskLevel.FORBIDDEN

    # ⚠️ 2. Check for IP-based URLs instead of domain names (High Risk)
    # Attackers often use raw IPs to hide their domain identity
    raw_ip_pattern = re.compile(r"https?://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")
    if raw_ip_pattern.search(url_lower):
        logger.info(f"⚠️ HIGH RISK: Navigation via raw IP address: {url}")
        return RiskLevel.HIGH

    return RiskLevel.SAFE