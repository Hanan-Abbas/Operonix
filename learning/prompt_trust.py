"""
learning/prompt_trust.py
─────────────────────────
Adaptive Trust Layer — the "personalised OS companion" brain for interactive
command prompts.

Responsibilities
────────────────
1. Contextual Observation
   Every time the user manually responds to an interactive prompt (y/n or
   free-text), the interaction is recorded: command pattern, prompt type,
   user response, and timestamp.

2. Approval Ratio / Trust Quotient
   For each command pattern (normalised, e.g. "apt install *"), the system
   tracks consecutive approvals.  Once a configurable threshold is reached
   (default: 5 consecutive "y" responses), the pattern is flagged Trusted.

3. Autonomous Transition — proactive suggestion
   On the *next* occurrence of a Trusted pattern, instead of asking the user,
   Operonix publishes a "trust_auto_approve_suggestion" event which the panel
   displays as: "I've noticed you always approve this.  Should I handle
   'apt install' automatically from now on?"

   If the user says Yes → the pattern is promoted to AutoApproved and future
   occurrences are answered automatically (no widget shown).
   If the user says No  → trust count resets; normal prompt resumes.

4. Persistence
   Trust data is stored in a JSON file alongside the existing
   override_rankings.json so it survives restarts.

Public API
──────────
    trust = PromptTrustLayer()
    trust.start()                               # subscribe to bus events

    # Called by process_bridge / shell_tool before showing a prompt:
    action = trust.evaluate(command, prompt_type)
    # Returns: "prompt" | "suggest" | "auto"

    # Called after a user responds manually:
    trust.record_response(command, prompt_type, response)  # "y" | "n" | text

    # Called when user answers the suggestion:
    trust.record_suggestion_response(command, accept: bool)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.event_bus import bus

log = logging.getLogger("PromptTrustLayer")

# ── Configuration ─────────────────────────────────────────────────────────────

TRUST_THRESHOLD    = int(os.getenv("PROMPT_TRUST_THRESHOLD", "5"))   # consecutive approvals
STORE_PATH         = Path(__file__).parent / "prompt_trust.json"

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class _TrustRecord:
    pattern:              str                          # normalised command pattern
    prompt_type:          str                          # "yn" | "freetext" | "password"
    consecutive_approvals: int      = 0
    total_approvals:      int       = 0
    total_denials:        int       = 0
    state:                Literal["observe", "suggest", "auto"] = "observe"
    last_seen:            float     = field(default_factory=time.time)
    suggested_at:         float     = 0.0              # timestamp of last suggestion


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalise_command(command: str) -> str:
    """
    Reduce a command to a stable pattern for storage and lookup.

    Examples:
      "sudo apt install ncdu"       → "sudo apt install *"
      "sudo apt install vim git"    → "sudo apt install *"
      "sudo apt-get remove python3" → "sudo apt-get remove *"
      "pip install requests==2.28"  → "pip install *"
      "rm -rf /tmp/mydir"           → "rm -rf *"
    """
    cmd = command.strip()

    # Package manager: keep the sub-command, replace package names
    pkg_mgr = re.match(
        r'^(sudo\s+)?(apt(?:-get)?|dnf|yum|pacman|pip3?|npm|yarn|snap|flatpak)\s+'
        r'(install|remove|uninstall|update|upgrade|add|delete)\s+',
        cmd, re.I,
    )
    if pkg_mgr:
        return pkg_mgr.group(0).rstrip() + " *"

    # git push/pull — keep remote/branch pattern
    git = re.match(r'^git\s+(push|pull|commit|merge|rebase)\b', cmd, re.I)
    if git:
        return git.group(0) + " *"

    # rm -rf — keep flags, replace path
    rm = re.match(r'^(sudo\s+)?rm\s+([-\w]+\s+)+', cmd, re.I)
    if rm:
        return rm.group(0).rstrip() + " *"

    # Default: keep first two tokens
    tokens = cmd.split()
    prefix = " ".join(tokens[:2]) if len(tokens) >= 2 else cmd
    return prefix + " *"


# ── Trust layer ───────────────────────────────────────────────────────────────

class PromptTrustLayer:
    """Adaptive Trust Layer for interactive command prompts."""

    def __init__(self) -> None:
        self._records: dict[str, _TrustRecord] = {}
        self._load()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe to EventBus events."""
        bus.subscribe("interactive_prompt_responded",   self._on_manual_response)
        bus.subscribe("trust_suggestion_answered",      self._on_suggestion_answered)
        log.info("PromptTrustLayer: online (threshold=%d)", TRUST_THRESHOLD)

    # ── Main decision method ───────────────────────────────────────────────────

    def evaluate(self, command: str, prompt_type: str) -> Literal["prompt", "suggest", "auto"]:
        """
        Decide how to handle the next interactive prompt for this command.

        Returns:
          "auto"    — this pattern is fully trusted; send the default response
                      automatically without showing any widget.
          "suggest" — threshold reached on this occurrence; show the suggestion
                      widget asking if the user wants to automate it.
          "prompt"  — normal interactive prompt; show the widget and wait.
        """
        key    = self._key(command, prompt_type)
        record = self._records.get(key)

        if record is None:
            return "prompt"

        if record.state == "auto":
            log.info("PromptTrustLayer: AUTO for pattern=%r", record.pattern)
            return "auto"

        if record.state == "suggest":
            # We already suggested but the user didn't answer yet — re-suggest
            return "suggest"

        # Check if threshold is reached this time
        if record.consecutive_approvals >= TRUST_THRESHOLD:
            record.state      = "suggest"
            record.suggested_at = time.time()
            self._save()
            log.info(
                "PromptTrustLayer: threshold reached for pattern=%r — suggesting automation",
                record.pattern,
            )
            return "suggest"

        return "prompt"

    def get_auto_response(self, command: str, prompt_type: str) -> str:
        """Return the stored automatic response for a trusted pattern."""
        key    = self._key(command, prompt_type)
        record = self._records.get(key)
        if record and record.state == "auto":
            return "y" if prompt_type == "yn" else ""
        return "y"  # safe default

    # ── Recording ─────────────────────────────────────────────────────────────

    def record_response(
        self,
        command:     str,
        prompt_type: str,
        response:    str,
    ) -> None:
        """
        Record the user's manual response to an interactive prompt.
        Called by shell_tool/process_bridge after the user responds.
        """
        key     = self._key(command, prompt_type)
        pattern = _normalise_command(command)

        if key not in self._records:
            self._records[key] = _TrustRecord(pattern=pattern, prompt_type=prompt_type)

        record           = self._records[key]
        record.last_seen = time.time()
        is_approval      = response.strip().lower() in ("y", "yes", "")

        if is_approval:
            record.consecutive_approvals += 1
            record.total_approvals       += 1
            log.debug(
                "PromptTrustLayer: approval #%d for pattern=%r",
                record.consecutive_approvals, pattern,
            )
        else:
            # Denial resets consecutive counter and trust state
            record.consecutive_approvals = 0
            record.total_denials        += 1
            if record.state in ("suggest", "auto"):
                record.state = "observe"
                log.info(
                    "PromptTrustLayer: trust RESET for pattern=%r (user denied)",
                    pattern,
                )

        self._save()

        # Publish for dashboard visibility
        bus.publish(
            "trust_record_updated",
            {
                "pattern":              record.pattern,
                "prompt_type":          record.prompt_type,
                "consecutive_approvals": record.consecutive_approvals,
                "state":                record.state,
            },
            source="prompt_trust",
        )

    def record_suggestion_response(
        self,
        command:     str,
        prompt_type: str,
        accept:      bool,
    ) -> None:
        """
        User answered the "Should I automate this?" suggestion.
        Called by panel_controller when the user clicks Yes or No on the
        trust suggestion widget.
        """
        key    = self._key(command, prompt_type)
        record = self._records.get(key)
        if record is None:
            return

        if accept:
            record.state = "auto"
            log.info(
                "PromptTrustLayer: pattern=%r promoted to AUTO by user",
                record.pattern,
            )
            bus.publish(
                "trust_pattern_automated",
                {"pattern": record.pattern, "prompt_type": record.prompt_type},
                source="prompt_trust",
            )
        else:
            record.state                 = "observe"
            record.consecutive_approvals = 0
            log.info(
                "PromptTrustLayer: user declined automation for pattern=%r — resetting",
                record.pattern,
            )

        self._save()

    # ── EventBus handlers ─────────────────────────────────────────────────────

    async def _on_manual_response(self, event: Any) -> None:
        """
        Bus event: interactive_prompt_responded
        Published by shell_tool after the user manually answers a prompt.
        Payload: {task_id, command, prompt_type, response}
        """
        data        = event.data if hasattr(event, "data") else {}
        command     = data.get("command", "")
        prompt_type = data.get("prompt_type", "yn")
        response    = data.get("response", "")
        if command:
            self.record_response(command, prompt_type, response)

    async def _on_suggestion_answered(self, event: Any) -> None:
        """
        Bus event: trust_suggestion_answered
        Published by panel_controller when user clicks Yes/No on suggestion.
        Payload: {command, prompt_type, accept: bool}
        """
        data        = event.data if hasattr(event, "data") else {}
        command     = data.get("command", "")
        prompt_type = data.get("prompt_type", "yn")
        accept      = bool(data.get("accept", False))
        if command:
            self.record_suggestion_response(command, prompt_type, accept)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _key(self, command: str, prompt_type: str) -> str:
        return f"{_normalise_command(command)}::{prompt_type}"

    def _load(self) -> None:
        if not STORE_PATH.exists():
            return
        try:
            raw = json.loads(STORE_PATH.read_text(encoding="utf-8"))
            for key, val in raw.items():
                # Skip namespace keys that are not _TrustRecord dicts.
                # 'categories' is written by sandbox_runner for permanent
                # plugin-generation rules — it is a dict-of-dicts, not a
                # _TrustRecord, and must not be passed to _TrustRecord(**val).
                if not isinstance(val, dict) or "pattern" not in val:
                    continue
                try:
                    self._records[key] = _TrustRecord(**val)
                except TypeError:
                    # Unknown fields in future schema versions — skip gracefully
                    pass
            log.info("PromptTrustLayer: loaded %d trust record(s)", len(self._records))
        except Exception as exc:
            log.warning("PromptTrustLayer: could not load store — %s", exc)

    def _save(self) -> None:
        try:
            STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {k: asdict(v) for k, v in self._records.items()}
            STORE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("PromptTrustLayer: could not save store — %s", exc)

    def get_all_records(self) -> list[dict]:
        """Return all trust records for dashboard display."""
        return [asdict(r) for r in self._records.values()]


# Singleton
prompt_trust = PromptTrustLayer()