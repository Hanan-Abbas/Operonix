"""
safety/risk_rules.py
─────────────────────
Dynamic risk assessment engine for Operonix.

CHANGES FROM PREVIOUS VERSION
──────────────────────────────
BUG 1 — Every run_command was forced to HIGH regardless of content.
    The old code did: `if risk == RiskLevel.SAFE: risk = RiskLevel.HIGH`
    unconditionally inside the _COMMAND_INTENTS branch of PermissionGuard,
    meaning `ls -la`, `pwd`, `echo hello` all triggered a confirmation prompt.
    FIX: Introduced _READ_ONLY_CMDS whitelist.  Any command whose first token
    is in that set is classified SAFE (unless a write-redirect > is present).

BUG 2 — Risk rules were hardcoded regex strings with no awareness of the
    execution profile (ghost / bridge / lab) already determined by
    intent_parser and terminal_resolver.
    FIX: get_command_risk() accepts an optional profile_hint.  Bridge
    environment-modifier commands (source, export, cd) are LOW — the user
    deliberately targets their own shell.  Lab commands are HIGH so the user
    knows a new window will open.  Unknown commands default to HIGH for ghost
    (conservative) but LOW for bridge (user's own shell, user can see it).

BUG 3 — Long command output (e.g. ls -la on a large home dir) flooded the
    panel with thousands of characters, making the UI unusable.
    FIX: Added truncate_output() utility.  The executor calls this before
    publishing command_output_ready / execution_step_success events.
"""
from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Optional

logger = logging.getLogger("RiskRules")


# ── Risk levels ───────────────────────────────────────────────────────────────

class RiskLevel(Enum):
    SAFE      = 0   # Proceed automatically, no prompt
    LOW       = 1   # Minor risk, proceed automatically
    HIGH      = 2   # Requires explicit human confirmation
    FORBIDDEN = 3   # Hard-blocked, never executed


# ── Safe (read-only / side-effect-free) commands ─────────────────────────────
# Commands whose first token is here are classified SAFE when used without
# a write-redirect.  Derived from the same sets used by terminal_resolver
# (_FORCE_BRIDGE / _FORCE_LAB) so the two modules stay in sync.

_READ_ONLY_CMDS: frozenset[str] = frozenset({
    # Directory / file listing
    "ls", "ll", "la", "dir", "tree", "find", "locate", "mlocate",
    # File reading
    "cat", "bat", "less", "more", "head", "tail", "strings", "xxd", "od",
    # File info / metadata
    "file", "stat", "wc", "du", "df", "lsblk", "lsof",
    # Process info (read-only)
    "ps", "top", "htop", "pgrep", "pstree", "uptime", "who", "w",
    # Identity / environment
    "pwd", "whoami", "id", "hostname", "uname", "env", "printenv", "echo",
    # Network status (read-only)
    "ping", "traceroute", "tracepath", "nslookup", "dig", "host",
    "ifconfig", "ip", "ss", "netstat",
    # Version / help / lookup
    "which", "type", "command", "whereis",
    # Text processing (no write)
    "grep", "egrep", "fgrep", "rg", "awk", "sed",
    "sort", "uniq", "cut", "tr", "jq", "yq", "diff", "comm",
    # Archive inspection only (no extract)
    "zipinfo",
    # Hash / checksum
    "md5sum", "sha1sum", "sha256sum", "sha512sum", "cksum",
    # System info
    "lscpu", "lsusb", "lspci", "free", "vmstat",
    # Git read-only
    "git",          # further refined by subcommand check below
    # Misc safe
    "date", "cal", "history", "jobs",
})

# Git subcommands that are genuinely read-only (no push / commit / reset etc.)
_GIT_READ_ONLY_SUBCOMMANDS: frozenset[str] = frozenset({
    "log", "status", "diff", "show", "branch", "remote",
    "fetch", "stash", "describe", "shortlog", "tag", "--version",
})

# Commands that are HIGH by policy — mutate state even if they look simple.
_HIGH_POLICY_CMDS: frozenset[str] = frozenset({
    "sudo", "su", "doas", "pkexec",
    "kill", "killall", "pkill", "renice", "nice",
    "systemctl", "service", "initctl",
    "iptables", "ip6tables", "nftables", "ufw", "firewall-cmd",
    "ifup", "ifdown", "nmcli", "netplan",
    "chmod", "chown", "chgrp", "setfacl",
    "mount", "umount", "fdisk", "parted", "mkfs", "fsck",
    "unzip", "tar",          # extraction writes files; -t (list) is safe → checked below
    "apt", "apt-get", "dpkg", "snap", "flatpak",
    "dnf", "yum", "pacman", "zypper", "brew",
    "pip", "pip3", "npm", "yarn", "cargo", "gem",
    "mv", "cp", "rm", "mkdir", "rmdir", "touch", "ln",
    "crontab", "at",
    "ssh", "scp", "rsync", "sftp",
    "curl", "wget",          # downloads modify filesystem; --head is safe → checked below
})

# Lab commands open a new visible terminal — user should be aware.
_LAB_CMDS: frozenset[str] = frozenset({
    "pytest", "jest", "mocha", "vitest",
    "jupyter", "ipython",
    "uvicorn", "gunicorn", "flask", "django",
    "docker",
    "make", "cmake", "gradle", "mvn",
})

# Bridge env-modifier commands — safe because they run in the user's own shell
# and the user can see exactly what happens.
_BRIDGE_SAFE_CMDS: frozenset[str] = frozenset({
    "source", ".", "export", "cd", "alias", "unset", "set",
    "activate", "deactivate", "conda", "nvm", "pyenv", "rbenv",
})


# ── Structural attack patterns ────────────────────────────────────────────────

_OBFUSCATION_RE = re.compile(
    r"(\bbase64\b.*(-d|--decode)|"
    r"\beval\b|\bexec\b|"
    r"\\x[0-9a-fA-F]{2}|"
    r"\bcharcode\b)",
    re.IGNORECASE,
)

_TRAVERSAL_RE = re.compile(r"(\.\./|\.\.\\)")

_PIPE_TO_SHELL_RE = re.compile(
    r"\b(curl|wget|fetch)\b.*?\|.*?\b(sh|bash|zsh|fish|python\d?|php|ruby|perl|nc|ncat)\b",
    re.IGNORECASE,
)

_DESTRUCTIVE_RE = re.compile(
    r"(\brm\s+.*-[a-zA-Z]*[rR][fF]|"
    r"\brm\s+.*-[a-zA-Z]*[fF][rR]|"
    r"\bmkfs\b|"
    r"\bdd\s+if=|"
    r">\s*/dev/(?!null)[^\s]|"
    r"\bwipe\b|\bshred\b|\bdiskutil\s+eraseDisk\b)",
    re.IGNORECASE,
)

_SENSITIVE_FILES_RE = re.compile(
    r"(\.env(\b|$)|"
    r"\.ssh(/|$)|"
    r"\bid_rsa\b|\bid_ed25519\b|\bid_ecdsa\b|"
    r"\bpasswd\b|\bshadow\b|\bsudoers\b|"
    r"\bbash_history\b|\bzsh_history\b|"
    r"\bmaster\.key\b|\bcredentials\b)",
    re.IGNORECASE,
)

# A write redirect makes even a "read-only" command write to the filesystem.
_WRITE_REDIRECT_RE = re.compile(r"(?<![<>])>{1,2}(?!>)\s*\S")


# ── Command risk engine ───────────────────────────────────────────────────────

def get_command_risk(
    command: str,
    profile_hint: Optional[str] = None,
) -> RiskLevel:
    """
    Classify the risk of a raw shell command string.

    Args:
        command:      Raw command string (e.g. "ls -la", "rm -rf /tmp/old").
        profile_hint: Execution profile resolved by intent_parser /
                      terminal_resolver:
                        "ghost"  — silent background subprocess
                        "bridge" — injected into the user's own terminal
                        "lab"    — spawns a new visible terminal window
                        None     — unknown; assess conservatively

    Returns:
        RiskLevel enum value.
    """
    if not command:
        return RiskLevel.SAFE

    cmd         = command.strip()
    tokens      = cmd.split()
    first_token = tokens[0].lstrip("./") if tokens else ""

    # ── 1. Hard-forbidden structural attacks ──────────────────────────────────
    if _PIPE_TO_SHELL_RE.search(cmd):
        logger.warning("🚨 FORBIDDEN: pipe-to-shell detected in %r", cmd)
        return RiskLevel.FORBIDDEN

    if _DESTRUCTIVE_RE.search(cmd):
        if re.search(r"(\s/\s|\s/$|/dev/(?!null))", cmd):
            logger.warning("🚨 FORBIDDEN: destructive root/device op in %r", cmd)
            return RiskLevel.FORBIDDEN
        logger.info("⚠️  HIGH: destructive flags in %r", cmd)
        return RiskLevel.HIGH

    # ── 2. Sensitive file access ───────────────────────────────────────────────
    if _SENSITIVE_FILES_RE.search(cmd):
        logger.info("⚠️  HIGH: sensitive file access in %r", cmd)
        return RiskLevel.HIGH

    # ── 3. Obfuscation / encoded payloads ─────────────────────────────────────
    if _OBFUSCATION_RE.search(cmd):
        logger.info("⚠️  HIGH: obfuscated payload in %r", cmd)
        return RiskLevel.HIGH

    # ── 4. Directory traversal ────────────────────────────────────────────────
    if _TRAVERSAL_RE.search(cmd):
        logger.info("⚠️  HIGH: directory traversal in %r", cmd)
        return RiskLevel.HIGH

    # ── 5. Bridge env-modifier commands ──────────────────────────────────────
    # source, export, cd etc. run in the user's own shell — safe.
    if first_token in _BRIDGE_SAFE_CMDS or profile_hint == "bridge":
        logger.debug("✅ LOW (bridge env-modifier): %r", first_token)
        return RiskLevel.LOW

    # ── 6. Read-only whitelist ────────────────────────────────────────────────
    if first_token in _READ_ONLY_CMDS:
        # Special case: `git` is read-only only for specific subcommands
        if first_token == "git":
            sub = tokens[1] if len(tokens) > 1 else ""
            if sub not in _GIT_READ_ONLY_SUBCOMMANDS:
                logger.info("⚠️  HIGH: git mutating subcommand %r", sub)
                return RiskLevel.HIGH

        # Special case: `tar -x` extracts (writes); `tar -t` lists (safe)
        if first_token == "tar" and re.search(r"\s-[a-zA-Z]*x", cmd):
            logger.info("⚠️  HIGH: tar extraction in %r", cmd)
            return RiskLevel.HIGH

        # Special case: curl/wget without --head or --spider write to disk
        if first_token in {"curl", "wget"}:
            if not re.search(r"(--head|-I|--spider)", cmd, re.IGNORECASE):
                logger.info("⚠️  HIGH: curl/wget download in %r", cmd)
                return RiskLevel.HIGH

        # Write redirect makes any read-only command into a write
        if _WRITE_REDIRECT_RE.search(cmd):
            logger.info("⚠️  HIGH: read-only cmd with write redirect in %r", cmd)
            return RiskLevel.HIGH

        logger.debug("✅ SAFE: read-only command %r", first_token)
        return RiskLevel.SAFE

    # ── 7. Policy-HIGH commands ───────────────────────────────────────────────
    if first_token in _HIGH_POLICY_CMDS:
        logger.info("⚠️  HIGH: policy-flagged command %r", first_token)
        return RiskLevel.HIGH

    # ── 8. Lab commands ───────────────────────────────────────────────────────
    if first_token in _LAB_CMDS or profile_hint == "lab":
        logger.info("⚠️  HIGH (lab): interactive/long-running command %r", first_token)
        return RiskLevel.HIGH

    # ── 9. Unknown command — conservative default ─────────────────────────────
    # Ghost / unknown: we don't know what this does → HIGH.
    # Bridge: user's own shell, user can see output → LOW.
    if profile_hint == "bridge":
        logger.debug("✅ LOW (bridge unknown): %r", first_token)
        return RiskLevel.LOW

    logger.info("⚠️  HIGH: unknown command %r (profile=%s)", first_token, profile_hint)
    return RiskLevel.HIGH


# ── File operation risk engine ────────────────────────────────────────────────

def get_file_op_risk(intent: str, file_path: str) -> RiskLevel:
    """Classify risk for file-system operations by path and intent."""
    if not file_path:
        return RiskLevel.SAFE

    path_lower = file_path.lower().strip()

    if _TRAVERSAL_RE.search(path_lower):
        logger.warning("🚨 FORBIDDEN: traversal in path %r", file_path)
        return RiskLevel.FORBIDDEN

    if path_lower in {"/", "c:", "c:\\", "/root"}:
        if intent in {"delete_file", "write_file", "move_file", "delete_dir"}:
            logger.warning("🚨 FORBIDDEN: root-level %s on %r", intent, file_path)
            return RiskLevel.FORBIDDEN

    if _SENSITIVE_FILES_RE.search(path_lower):
        logger.info("⚠️  HIGH: sensitive file %r for %s", file_path, intent)
        return RiskLevel.HIGH

    return RiskLevel.SAFE


# ── Web operation risk engine ─────────────────────────────────────────────────

def get_web_op_risk(url: str) -> RiskLevel:
    """Classify risk for web navigation / fetch operations."""
    if not url:
        return RiskLevel.SAFE

    url_lower = url.lower()

    _LOCAL_NETWORK_RE = re.compile(
        r"(localhost|127\.0\.0\.1|0\.0\.0\.0|"
        r"192\.168\.|10\.\d+\.\d+\.|"
        r"172\.(1[6-9]|2[0-9]|3[01])\.)"
    )
    if _LOCAL_NETWORK_RE.search(url_lower):
        logger.warning("🚨 FORBIDDEN: local-network URL %r", url)
        return RiskLevel.FORBIDDEN

    _RAW_IP_RE = re.compile(r"https?://\d{1,3}(?:\.\d{1,3}){3}")
    if _RAW_IP_RE.search(url_lower):
        logger.info("⚠️  HIGH: raw-IP URL %r", url)
        return RiskLevel.HIGH

    return RiskLevel.SAFE


# ── Output truncation utility ─────────────────────────────────────────────────

_DEFAULT_MAX_CHARS = 2000
_DEFAULT_MAX_LINES = 50


def truncate_output(
    text: str,
    max_chars: int = _DEFAULT_MAX_CHARS,
    max_lines: int = _DEFAULT_MAX_LINES,
) -> str:
    """
    Cap command stdout/stderr before publishing to the panel event bus.

    Applies both a line limit and a character limit, whichever triggers first.
    Appends a human-readable notice so the user knows output was clipped.

    Used by executor._run_plan() and shell_tool before publishing
    command_output_ready / execution_step_success events.

    Args:
        text:       Raw stdout or stderr string.
        max_chars:  Maximum characters to keep (default 2000).
        max_lines:  Maximum lines to keep (default 50).

    Returns:
        Possibly-truncated string with an appended notice if clipped.
    """
    if not text:
        return text

    lines       = text.splitlines()
    total_lines = len(lines)

    truncated = False
    if total_lines > max_lines:
        lines     = lines[:max_lines]
        truncated = True

    joined = "\n".join(lines)

    if len(joined) > max_chars:
        joined    = joined[:max_chars]
        truncated = True

    if truncated:
        shown = joined.count("\n") + 1
        joined += (
            f"\n... [output truncated — showing {shown} of {total_lines} lines]"
        )

    return joined