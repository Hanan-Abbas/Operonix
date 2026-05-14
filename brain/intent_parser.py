"""
brain/intent_parser.py

Validation layer: ensures LLM-interpreted intents exist in the capability
registry and evaluates risk before allowing execution.

Panel integration
─────────────────
The suggestion engine calls `intent_parser.parse(text)` synchronously
(wrapped in asyncio.to_thread) to power live suggestions as the user types.
`parse()` is a thin synchronous wrapper that runs the async `_parse_async`
pipeline in a safe way — it never blocks the Qt thread.

HYBRID EXECUTION CHANGE:
  _parse_async() now asks the LLM for an additional "profile" key alongside
  intent/confidence/parameters.  Valid values: "ghost", "bridge", "lab", None.
  This is stored in the returned dict and forwarded through the orchestrator
  into the executor, which injects it into args["profile_hint"] before calling
  terminal_resolver.resolve().

  Commands like "source", "export", "activate" are keyword-forced to "bridge"
  in _keyword_profile_hint() so they are correct even when the LLM is offline.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from core.config import settings
from core.event_bus import bus
from memory.vector_store import vector_store
from brain.llm_client import llm_client


# Commands that MUST run in the user's shell (Bridge profile).
# Source, export, cd etc. modify the calling shell's environment — running
# them in a silent subprocess is always wrong.
_BRIDGE_KEYWORDS: frozenset[str] = frozenset({
    "source", "activate", "deactivate", "export", "unset",
    "cd", "alias", "nvm", "pyenv", "rbenv", "conda",
})

# Commands that require a password prompt shown in the panel (panel_sudo profile).
# These run via `sudo -S` with stdin piped from the panel password widget.
# They must NOT go to Bridge (which injects blind text into another terminal)
# and must NOT go to Ghost (which has no stdin).
_PANEL_SUDO_KEYWORDS: frozenset[str] = frozenset({
    "sudo",
    "apt", "apt-get", "dpkg",
    "snap", "flatpak",
    "dnf", "yum", "pacman", "zypper", "brew",
})

# Commands that benefit from a visible interactive terminal (Lab profile).
_LAB_KEYWORDS: frozenset[str] = frozenset({
    "pytest", "npm run", "yarn", "make", "docker", "jupyter",
    "ipython", "uvicorn", "gunicorn", "flask", "django",
    "manage.py", "cargo", "go run", "mvn", "gradle",
})


class IntentParser:
    """
    🔍 The Validation Layer.
    Ensures that the LLM's interpreted intent exists in our capability registry
    and evaluates the risk level before allowing execution.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("IntentParser")

    async def start(self) -> None:
        """Subscribe to the output of the LLM Client."""
        bus.subscribe("intent_parsed", self.validate_and_route)
        self.logger.info("🛡️ Intent Parser active: Monitoring LLM output...")

        # Index built-in capability intents into vector store
        try:
            from capabilities.registry import capability_registry
            supported = capability_registry.get_all_names()
        except Exception:
            self.logger.warning("Capability registry not found. Waiting for dynamic registration.")
            supported = []

        if supported:
            await vector_store.add_intents(supported)

        # Index plugin capabilities into vector store so _resolve_intent
        # can find them via semantic search (Tier 2) and local match (Tier 3).
        # Plugins live in plugin_registry which is separate from capability_registry.
        try:
            from plugins.loader import plugin_loader
            plugin_intents: list[str] = []
            for entry in plugin_loader.list_plugins():
                caps = entry.get("capabilities", [])
                plugin_intents.extend(str(c) for c in caps if c)
                # Also add the plugin name itself as a resolvable intent
                if entry.get("name"):
                    plugin_intents.append(entry["name"])
            if plugin_intents:
                await vector_store.add_intents(plugin_intents)
                self.logger.info(
                    "🔌 Indexed %d plugin intent(s) into vector store.",
                    len(plugin_intents),
                )
        except Exception as exc:
            self.logger.debug("Could not index plugin intents: %s", exc)

        # Re-index whenever a new plugin is deployed or hot-reloaded
        bus.subscribe("plugin_deployed",     self._on_plugin_registry_changed)
        bus.subscribe("plugin_hot_reloaded", self._on_plugin_registry_changed)

    # ── Panel-facing synchronous interface ────────────────────────────────────

    def parse(self, text: str) -> dict[str, Any]:
        """
        Synchronous entry point called by the panel's suggestion engine.
        Runs the async pipeline in a new event loop if no loop is running,
        or schedules it safely if one is already running.

        Returns a dict with at minimum:
            {
                "intent":       str | None,
                "confidence":   float,          # 0.0 – 1.0
                "parameters":   dict,
                "profile_hint": str | None,     # "ghost" | "bridge" | "lab" | None
            }
        """
        try:
            # If there is already a running loop (asyncio or Qt async bridge)
            # we cannot call run_until_complete — use a new thread-level loop.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # We're inside an async context — run in a fresh OS thread loop.
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self._run_parse_sync, text)
                    return future.result(timeout=5.0)
            else:
                return asyncio.run(self._parse_async(text))

        except Exception as exc:  # noqa: BLE001
            self.logger.warning("IntentParser.parse: failed for %r — %s", text, exc)
            return {
                "intent":       None,
                "confidence":   0.0,
                "parameters":   {},
                "profile_hint": None,
            }

    def _run_parse_sync(self, text: str) -> dict[str, Any]:
        """Run _parse_async in a brand-new event loop (used from non-async threads)."""
        return asyncio.run(self._parse_async(text))

    async def _parse_async(self, text: str) -> dict[str, Any]:
        """
        Async core of parse(): asks the LLM to classify the intent,
        then resolves and validates it against the registry.

        The LLM prompt now requests an additional "profile" field so the
        executor knows whether to run Ghost / Bridge / Lab without a second
        LLM call.
        """
        if not text.strip():
            return {
                "intent":       None,
                "confidence":   0.0,
                "parameters":   {},
                "profile_hint": None,
            }

        # ── Keyword-based profile hint (works offline) ────────────────────
        # Apply before the LLM call so even degraded mode gets this right.
        keyword_profile = self._keyword_profile_hint(text)

        prompt = f"""
Extract the intent, parameters, and execution profile from this user command.
Return ONLY JSON with these keys:
  intent       — snake_case string capturing the FULL action, not just a single verb.
                 WRONG: "click" for "start auto clicker"   RIGHT: "auto_clicker"
                 WRONG: "open"  for "open chrome"          RIGHT: "open_chrome"
                 WRONG: "open"  for "open the app cursor"  RIGHT: "app_opening"
                 WRONG: "run"   for "run pytest"           RIGHT: "run_command"
                 CRITICAL: "open <app>" or "launch <app>" requests are ALWAYS
                 intent="app_opening" with parameters={{"app_name": "<app>"}}.
                 NEVER use intent="run_command" for opening GUI applications.
                 Preserve compound/multi-word intents as snake_case.
  confidence   — float 0.0-1.0
  parameters   — dict of specific values mentioned (path, interval, hotkey, url, query, command, app_name, etc.)
  profile      — one of "ghost", "bridge", "lab", or null
                 ghost  = run silently in background (output piped, no visible terminal)
                 bridge = inject into user's active terminal (must use for: source, export, cd, activate, conda, nvm)
                 lab    = spawn new visible terminal window (use for: pytest, jupyter, docker, long-running servers)
                 null   = let the system decide

User command: "{text}"

Example:
  Input:  "source venv/bin/activate"
  Output: {{"intent": "run_command", "confidence": 0.97, "parameters": {{"command": "source venv/bin/activate"}}, "profile": "bridge"}}

Example:
  Input:  "run pytest on the tests folder"
  Output: {{"intent": "run_command", "confidence": 0.91, "parameters": {{"command": "pytest tests/"}}, "profile": "lab"}}

Example:
  Input:  "list files in current directory"
  Output: {{"intent": "run_command", "confidence": 0.95, "parameters": {{"command": "ls -la"}}, "profile": "ghost"}}

Example:
  Input:  "open the app named gedit"
  Output: {{"intent": "app_opening", "confidence": 0.97, "parameters": {{"app_name": "gedit"}}, "profile": null}}

Example:
  Input:  "open chrome"
  Output: {{"intent": "app_opening", "confidence": 0.95, "parameters": {{"app_name": "google-chrome"}}, "profile": null}}

Example:
  Input:  "launch cursor"
  Output: {{"intent": "app_opening", "confidence": 0.96, "parameters": {{"app_name": "cursor"}}, "profile": null}}

Example:
  Input:  "start the auto clicker"
  Output: {{"intent": "auto_clicker", "confidence": 0.95, "parameters": {{"interval": 0.1, "stop_hotkey": "alt+s"}}, "profile": null}}
"""
        try:
            raw = await asyncio.wait_for(
                llm_client.generate(prompt, use_json=True),
                timeout=4.0,
            )
            if isinstance(raw, dict):
                llm_intent  = raw.get("intent", "")
                confidence  = float(raw.get("confidence", 0.5))
                parameters  = raw.get("parameters", {})
                llm_profile = raw.get("profile")
            else:
                llm_intent  = str(raw)
                confidence  = 0.5
                parameters  = {}
                llm_profile = None
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("_parse_async: LLM failed — %s", exc)
            # Graceful degradation: attempt a keyword-based guess.
            llm_intent  = self._keyword_guess(text)
            confidence  = 0.3 if llm_intent else 0.0
            parameters  = {}
            llm_profile = None

        # Keyword profile takes priority over LLM profile when the command
        # clearly requires a specific execution environment.
        effective_profile = keyword_profile or (
            llm_profile if llm_profile in ("ghost", "bridge", "lab") else None
        )

        # ── Post-processing: manifest-driven intent rerouting ────────────────
        # Each plugin declares intent_patterns in its manifest describing which
        # raw LLM intent+arg shapes should be rerouted to it.
        # This replaces ALL hardcoded reroute rules — adding a new plugin with
        # the right intent_patterns is sufficient; no parser changes needed.
        llm_intent, parameters = self._reroute_via_manifests(llm_intent, parameters)

        # Resolve against the capability registry (exact + semantic).
        resolved = await self._resolve_intent(llm_intent)
        if resolved:
            return {
                "intent":       resolved,
                "confidence":   confidence,
                "parameters":   parameters,
                "profile_hint": effective_profile,
            }

        # If resolution failed, still return the raw LLM guess so the
        # suggestion engine can show a low-confidence ui-fallback row.
        return {
            "intent":       llm_intent or None,
            "confidence":   confidence * 0.4,
            "parameters":   parameters,
            "profile_hint": effective_profile,
        }

    # ── Profile keyword detection ─────────────────────────────────────────────

    @staticmethod
    def _keyword_profile_hint(text: str) -> str | None:
        """
        Fast offline check — returns a forced profile if the first token of
        the command is in a known set.  Never calls the LLM.

        Priority order:
          1. panel_sudo — sudo / package managers that need a password typed
                          in the panel widget, piped to `sudo -S` stdin.
          2. bridge     — shell environment modifiers (source, export, cd…)
          3. lab        — long-running interactive processes (pytest, docker…)

        Called before _parse_async so even when the LLM is unavailable the
        profile hint is correct for the most critical cases.
        """
        # Strip natural-language preamble so "run the command sudo apt…"
        # correctly identifies "sudo" as the first token.
        stripped = text.strip()
        for prefix in ("run the command ", "execute the command ", "run ", "execute "):
            if stripped.lower().startswith(prefix):
                stripped = stripped[len(prefix):]
                break
        first_token = stripped.split()[0].lower() if stripped.split() else ""
        text_lower  = text.lower()

        # panel_sudo takes priority — these need the password widget
        if first_token in _PANEL_SUDO_KEYWORDS:
            return "panel_sudo"

        # package manager invoked via sudo (e.g. "sudo apt install …")
        # first token is "sudo", second might be a package manager — already
        # caught above since "sudo" is in _PANEL_SUDO_KEYWORDS.

        if first_token in _BRIDGE_KEYWORDS:
            return "bridge"
        if any(kw in text_lower for kw in _LAB_KEYWORDS):
            return "lab"
        return None

    @staticmethod
    def _keyword_guess(text: str) -> str:
        """
        Bare-minimum keyword matcher used only when the LLM is unavailable.
        Maps common English verbs to generic intent names.  Never hardcodes
        application-specific logic — just structural command words.

        "open/launch/start" are mapped to app_opening (not open_file) so the
        offline path routes app-launch requests to the correct plugin.
        The planner's _resolve_args_for_intent will fill app_name from context.
        """
        lower = text.lower()
        # App-open verbs checked before generic file ops
        if any(v in lower for v in ("open", "launch", "start")):
            # Distinguish "open a file/folder/url" from "open an app"
            _FILE_HINTS = ("file", "folder", "directory", ".txt", ".py", ".md", "http", "/")
            if any(h in lower for h in _FILE_HINTS):
                return "open_file"
            return "app_opening"
        mapping = {
            ("close", "quit", "exit", "kill"):      "close_app",
            ("search", "find", "look"):             "web_search",
            ("write", "create", "make", "new"):     "create_file",
            ("delete", "remove", "trash"):          "file_delete",
            ("read", "show", "display", "print"):   "read_file",
            ("edit", "modify", "change", "update"): "modify_code",
            ("copy", "duplicate"):                  "copy_file",
            ("move", "rename"):                     "move_file",
            ("run", "execute"):                     "run_command",
        }
        for verbs, intent in mapping.items():
            if any(v in lower for v in verbs):
                return intent
        return ""

    # ── EventBus path ─────────────────────────────────────────────────────────

    async def validate_and_route(self, event: Any) -> None:
        """Validates if the intent is supported and determines the next step."""
        task_id      = event.data.get("task_id")
        raw_intent   = event.data.get("intent")
        params       = event.data.get("parameters", {})
        # profile_hint may have been injected by the orchestrator from the
        # panel payload (set during parse()) or arrive via the event itself.
        profile_hint = event.data.get("profile_hint") or self._keyword_profile_hint(
            params.get("command", "")
        )

        # If profile is panel_sudo, stamp needs_password into params so the
        # executor and shell_tool know to show the panel password widget and
        # run via `sudo -S` with stdin piped from the panel.
        if profile_hint == "panel_sudo":
            params = {**params, "needs_password": True}

        self.logger.info(
            "🔍 Validating intent: '%s' for task %s (profile_hint=%s)",
            raw_intent, task_id, profile_hint,
        )

        resolved_intent = await self._resolve_intent(raw_intent)
        if not resolved_intent:
            self.logger.error("❌ Unknown intent: %s. Aborting task.", raw_intent)
            await bus.emit(
                "task_failed",
                {"task_id": task_id, "error": f"Unsupported intent: {raw_intent}"},
            )
            return

        requires_confirmation = await self._is_risky(resolved_intent, params)

        if requires_confirmation:
            self.logger.warning(
                "⚠️ High-risk operation detected: %s. Escalating.", resolved_intent
            )
            full_task = {
                "task_id":      task_id,
                "intent":       resolved_intent,
                "parameters":   params,
                "profile_hint": profile_hint,
                "steps": [
                    {
                        "action": resolved_intent,
                        "args": {
                            **params,
                            "profile_hint":  profile_hint,
                            "needs_password": profile_hint == "panel_sudo",
                        },
                    }
                ],
            }
            await bus.emit("confirmation_required", {
                "task_id":      task_id,
                "intent":       resolved_intent,
                "parameters":   params,
                "profile_hint": profile_hint,
                "risk_level":   "high",
                "reason":       f"High-risk intent '{resolved_intent}' requires your approval.",
                "step_data":    {"action": resolved_intent, "args": params},
                "full_task":    full_task,
            })
        else:
            await bus.emit("intent_validated", {
                "task_id":      task_id,
                "intent":       resolved_intent,
                "parameters":   params,
                "profile_hint": profile_hint,
            })

    async def _resolve_intent(self, raw_intent: str) -> str | None:
        """
        Resolution tiers (first hit wins):
          0. Exact or fuzzy match in plugin_registry capabilities
          1. Exact match in capability_registry (built-in capabilities)
          2. Semantic nearest-neighbour from vector store
          3. Local token/sequence matcher — used when vector store unavailable.

        Plugin registry is checked FIRST (Tier 0) because plugins fill gaps
        that built-in capabilities don't cover. Without this, resolved=None
        and the gap detector triggers re-generation of already-existing plugins.
        """
        if not raw_intent:
            return None

        normalized = raw_intent.lower().replace("_", " ").strip()

        # ── Tier 0: Plugin registry — check before built-in capabilities ──────
        # Plugins are in plugin_registry (separate from capability_registry).
        # Match against each plugin's capabilities list using local fuzzy match.
        plugin_caps: list[str] = []
        try:
            from plugins.loader import plugin_loader
            from brain.intent_matcher import match_intent_local
            # Map capability string → plugin name for lookup after match
            cap_to_plugin: dict[str, str] = {}
            for entry in plugin_loader.list_plugins():
                name = entry.get("name", "")
                for cap in entry.get("capabilities", []):
                    cap_str = str(cap).lower().replace("_", " ")
                    plugin_caps.append(cap_str)
                    cap_to_plugin[cap_str] = name
                # Also match against plugin name itself
                if name:
                    plugin_caps.append(name.replace("_", " "))
                    cap_to_plugin[name.replace("_", " ")] = name

            if plugin_caps:
                plugin_threshold = float(
                    getattr(settings, "PLUGIN_INTENT_MATCH_THRESHOLD", 0.55)
                )
                matched_cap, score = match_intent_local(
                    normalized, plugin_caps, threshold=plugin_threshold
                )
                # Substring fallback: if fuzzy match missed, check if normalized
                # is contained in any plugin cap or vice versa. Catches cases
                # where LLM returns a vague verb like "click" when the plugin
                # cap is "auto clicker" — "click" is in "auto clicker".
                if not matched_cap:
                    for cap in plugin_caps:
                        if (normalized in cap or cap in normalized) and len(normalized) >= 3:
                            matched_cap = cap
                            score = 0.6
                            break

                if matched_cap:
                    plugin_name = cap_to_plugin.get(matched_cap, matched_cap)
                    self.logger.info(
                        "🔌 Plugin-matched '%s' → plugin='%s' via cap='%s' (score=%.2f)",
                        raw_intent, plugin_name, matched_cap, score,
                    )
                    # Return the matched capability string — executor uses this
                    # to look up the plugin by capability in plugin_registry
                    return matched_cap
        except Exception as exc:
            self.logger.debug("Tier 0 plugin match failed: %s", exc)

        # ── Tier 1: Exact built-in capability hit ─────────────────────────────
        try:
            from capabilities.registry import capability_registry
            if capability_registry.get(raw_intent) is not None:
                return raw_intent
        except Exception:
            pass

        # Collect all known intents for tiers 2 & 3 (built-ins + plugins)
        known_intents: list[str] = []
        try:
            from capabilities.registry import capability_registry
            known_intents = capability_registry.get_all_names() or []
        except Exception:
            pass
        # Add plugin capabilities to known_intents for local matching
        if plugin_caps:  # already collected in Tier 0
            known_intents = known_intents + plugin_caps

        # ── Tier 2: Vector store semantic search ──────────────────────────────
        try:
            closest_intent, confidence = await vector_store.search_closest_intent(raw_intent)
            threshold = float(getattr(settings, "INTENT_MATCH_MIN_CONFIDENCE", 0.35))
            if closest_intent and confidence >= threshold:
                self.logger.info(
                    "🔎 Resolved '%s' → '%s' (confidence=%.2f)",
                    raw_intent, closest_intent, confidence,
                )
                return closest_intent
        except Exception as exc:
            self.logger.debug("_resolve_intent: vector search failed — %s", exc)

        # ── Tier 3: Local token/sequence matcher (no external deps) ───────────
        if known_intents:
            from brain.intent_matcher import match_intent_local
            local_threshold = float(getattr(settings, "INTENT_LOCAL_MATCH_THRESHOLD", 0.30))
            matched, score = match_intent_local(raw_intent, known_intents, threshold=local_threshold)
            if matched:
                self.logger.info(
                    "🔎 Local-matched '%s' → '%s' (score=%.2f)",
                    raw_intent, matched, score,
                )
                return matched

        return None

    # ── Manifest-driven helpers ───────────────────────────────────────────────

    @staticmethod
    def _load_all_manifests() -> list:
        """
        Load all installed plugin manifests from disk.
        Returns a list of PluginManifest objects.
        Cached per-call only — cheap enough for parse() frequency.
        """
        try:
            from plugins.loader import plugin_loader
            from plugins.manifest_schema import PluginManifest
            import os
            manifests = []
            for entry in plugin_loader.list_plugins():
                plugin_dir = entry.get("plugin_dir", "")
                if plugin_dir:
                    m = PluginManifest.load(plugin_dir)
                    if m:
                        manifests.append(m)
            return manifests
        except Exception:
            return []

    def _reroute_via_manifests(
        self, llm_intent: str, parameters: dict
    ) -> tuple[str, dict]:
        """
        Manifest-driven intent rerouting.

        Each plugin declares intent_patterns in its manifest:
          [{"raw_intents": ["run_command","open","launch"],
            "command_is_verb": true,
            "args_is_target": true}]

        When the LLM returns a misclassified intent that matches a pattern,
        this method reroutes to the correct plugin intent and normalizes
        the parameters using the plugin's parameter_schema.

        This replaces ALL hardcoded reroute rules in the intent parser.
        New plugins self-describe their patterns — no parser edits needed.
        """
        _LAUNCH_VERBS: frozenset[str] = frozenset({
            "open", "launch", "start", "run", "execute",
        })

        for manifest in self._load_all_manifests():
            if not manifest.intent_patterns:
                continue

            for pattern in manifest.intent_patterns:
                raw_intents = [r.lower() for r in (pattern.get("raw_intents") or [])]
                if llm_intent.lower() not in raw_intents:
                    continue

                # This manifest claims this raw intent — try to extract target
                command_is_verb = pattern.get("command_is_verb", False)
                args_is_target  = pattern.get("args_is_target", False)

                cmd       = str(parameters.get("command", "")).strip()
                args_list = parameters.get("args", [])

                target: str | None = None

                # Shape A: command is a verb, positional args[0] is the target
                if (
                    command_is_verb
                    and args_is_target
                    and cmd.lower() in _LAUNCH_VERBS
                    and isinstance(args_list, list)
                    and len(args_list) == 1
                    and isinstance(args_list[0], str)
                    and " " not in args_list[0].strip()
                    and not args_list[0].strip().startswith("-")
                ):
                    target = args_list[0].strip()

                # Shape B: command itself is the target (not a verb)
                elif (
                    command_is_verb
                    and cmd
                    and " " not in cmd
                    and cmd.lower() not in _LAUNCH_VERBS
                    and not cmd.startswith("-")
                    and "/" not in cmd
                    and "." not in cmd
                ):
                    target = cmd

                # Shape C: "open cursor" inline in command field
                elif command_is_verb and cmd and " " in cmd:
                    parts = cmd.split()
                    if (
                        len(parts) == 2
                        and parts[0].lower() in _LAUNCH_VERBS
                        and not parts[1].startswith("-")
                        and "/" not in parts[1]
                        and "." not in parts[1]
                    ):
                        target = parts[1]

                if target is None:
                    continue

                # Build normalized parameters from manifest parameter_schema
                new_params: dict = {}
                for param in (manifest.parameter_schema or []):
                    p_name     = param.get("name", "")
                    p_required = param.get("required", False)
                    p_aliases  = param.get("aliases", [])

                    # Check if already present under name or any alias
                    value = parameters.get(p_name)
                    if value is None:
                        for alias in p_aliases:
                            value = parameters.get(alias)
                            if value is not None:
                                break

                    # If still missing and this is the primary required param,
                    # use the extracted target value
                    if value is None and p_required:
                        value = target

                    if value is not None:
                        new_params[p_name] = value

                # If schema produced nothing useful, just put target under the
                # first required param name (or "target" as fallback)
                if not new_params:
                    schema = manifest.parameter_schema or []
                    first_required = next(
                        (p["name"] for p in schema if p.get("required")), "target"
                    )
                    new_params = {first_required: target}

                # Use the canonical intent from the manifest
                new_intent = manifest.capabilities[0] if manifest.capabilities else manifest.intent

                self.logger.debug(
                    "Manifest reroute: '%s' → '%s' via plugin '%s' (target=%r)",
                    llm_intent, new_intent, manifest.name, target,
                )
                return new_intent, new_params

        # No manifest claimed this intent — return unchanged
        return llm_intent, parameters

    def _get_manifest_risk(self, intent: str) -> str | None:
        """
        Look up the risk_level for an intent from installed plugin manifests.
        Returns "low", "medium", or "high" if a manifest matches, else None.
        Matching is done against manifest.capabilities and manifest.intent.
        """
        intent_normalized = intent.lower().replace("_", " ").strip()
        for manifest in self._load_all_manifests():
            # Match against declared capabilities
            for cap in (manifest.capabilities or []):
                if cap.lower().replace("_", " ").strip() == intent_normalized:
                    return manifest.risk_level.value if manifest.risk_level else None
            # Match against the primary intent field
            if manifest.intent.lower().replace("_", " ").strip() == intent_normalized:
                return manifest.risk_level.value if manifest.risk_level else None
        return None

    async def _on_plugin_registry_changed(self, event: Any) -> None:
        """
        Re-indexes plugin capabilities into the vector store whenever
        a plugin is deployed or hot-reloaded. Keeps _resolve_intent Tier 2
        (vector search) current without restarting the agent.
        """
        try:
            from plugins.loader import plugin_loader
            plugin_intents: list[str] = []
            for entry in plugin_loader.list_plugins():
                plugin_intents.extend(str(c) for c in entry.get("capabilities", []) if c)
                if entry.get("name"):
                    plugin_intents.append(entry["name"])
            if plugin_intents:
                await vector_store.add_intents(plugin_intents)
                self.logger.debug(
                    "Re-indexed %d plugin intent(s) after registry change.",
                    len(plugin_intents),
                )
        except Exception as exc:
            self.logger.debug("Could not re-index plugin intents: %s", exc)

    async def _is_risky(self, intent: str, params: dict) -> bool:
        """
        Evaluates if an action could damage the system or compromise security.

        Risk is determined from three sources in priority order:
          1. Plugin manifest risk_level — single source of truth for plugin intents.
             low  → never risky (no confirmation)
             medium/high → risky (confirmation required)
          2. RISKY_INTENTS config — for built-in capabilities with no plugin manifest.
          3. LLM scoring — last resort for unknown intents.

        No hardcoded intent names here. New plugins self-declare their risk.
        """
        # ── 1. Manifest-declared risk level (covers all plugin intents) ───────
        manifest_risk = self._get_manifest_risk(intent)
        if manifest_risk is not None:
            return manifest_risk in ("medium", "high")

        # ── 2. Configured RISKY_INTENTS (built-in capabilities) ──────────────
        if intent in getattr(settings, "RISKY_INTENTS", []):
            return True

        if intent == "modify_code":
            target_file = params.get("file_path", "")
            restricted_dirs = getattr(settings, "RESTRICTED_PATHS", ["core/", "safety/", "brain/"])
            if any(str(target_file).startswith(str(rd)) for rd in restricted_dirs):
                self.logger.warning("🛡️ Core modification attempt blocked: %s", target_file)
                return True

        # ── 3. LLM scoring — last resort ─────────────────────────────────────
        prompt = f"""
Rate the security risk of this AI operation (1-10):
Operation: {intent}
Parameters: {json.dumps(params)}

Rule: File deletions, code modifications, or raw shell commands are 8-10.
Rule: Reading files or checking system status are 1-3.
Rule: Launching GUI applications or opening apps is always 1-2.

Return ONLY JSON: {{"risk_score": <int>}}
"""
        try:
            response = await llm_client.generate(prompt, use_json=True)
            risk_score = response.get("risk_score", 1)
            return risk_score >= 7
        except Exception as exc:
            self.logger.error(
                "Risk evaluation failed: %s. Defaulting to SAFE/Locked mode.", exc
            )
            return True


# Global instance
intent_parser = IntentParser()