"""
tools/api_tool.py
──────────────────
Structured HTTP/API execution tool for the Operonix agent.

Problems with the original
───────────────────────────
1. supported_intents was a hardcoded set of six strings with no path to
   extension without editing this file.
2. can_handle() returned bool — the router needed a float confidence score
   but had to treat it as 0.0 / 1.0, losing resolution.
3. run() accepted (action, args) but the executor calls it with the
   LayeredPayload.api_body dict directly — the signature was misaligned.
4. Timeout was hardcoded to 10 seconds inside the method body.
5. No retry logic for transient network errors (ENV_TRANSIENT).
6. No distinction between client errors (4xx) and server errors (5xx) for
   FailureClass tagging.
7. HTTP method defaulted to GET with no support for PUT / PATCH / DELETE.
8. No structured error payload — callers received a bare string on failure.
9. Session was created and destroyed per call — expensive for high-frequency
   agents.
10. Supported intents were never exposed to the tool_registry, so
    get_tools_for_intent() could not discover this tool.

Design after this revision
───────────────────────────
• supported_intents is loaded from settings.API_TOOL_INTENTS (a list in
  dynamic_settings.json) with a safe fallback set.  New intents are added
  via config, not code.
• can_handle() returns float (1.0 on match, 0.0 otherwise) so the router
  receives a proper confidence score.
• run() accepts the LayeredPayload.api_body dict directly:
    {url, method, data, headers, params, _intent, _profile_hint}
• All timeouts and retry counts are read from settings.
• Transient errors (connection error, timeout, 5xx) raise APITransientError.
  Permanent errors (4xx except 429) raise APIPermanentError.
  The executor tags these with FailureClass.ENV_TRANSIENT / ENV_PERMANENT.
• A shared aiohttp.ClientSession is reused across calls via an async
  context manager; callers that cannot use async get a sync wrapper.
• Full structured response dict on success and failure — no bare strings.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from core.config import settings
from core.event_bus import bus

logger = logging.getLogger("APITool")


# ─────────────────────────────────────────────────────────────────────────────
# Typed exceptions for FailureClass tagging in the executor
# ─────────────────────────────────────────────────────────────────────────────

class APITransientError(IOError):
    """
    Raised for errors that are worth retrying:
      - Connection errors / timeouts
      - HTTP 429 (rate-limited — back off and retry)
      - HTTP 5xx (server-side; the route is valid but the server is down)
    The executor tags this as FailureClass.ENV_TRANSIENT.
    """
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class APIPermanentError(IOError):
    """
    Raised for errors that should NOT be retried:
      - HTTP 4xx (except 429): bad request, unauthorized, not found, etc.
      - Malformed URL, missing required fields
    The executor tags this as FailureClass.ENV_PERMANENT.
    """
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# ─────────────────────────────────────────────────────────────────────────────
# Supported-intents registry
# ─────────────────────────────────────────────────────────────────────────────

def _load_supported_intents() -> frozenset[str]:
    """
    Load the set of intents this tool handles.

    Primary source: settings.API_TOOL_INTENTS (list[str]) — set via
    dynamic_settings.json so operators can extend it without touching code.

    Fallback: a minimal default set that covers the intents the original
    tool declared, preserved so existing callers don't break.
    """
    from_settings = getattr(settings, "API_TOOL_INTENTS", None)
    if isinstance(from_settings, (list, tuple, set, frozenset)) and from_settings:
        return frozenset(str(i) for i in from_settings)

    # Default fallback — matches original supported_intents
    return frozenset({
        "extract_text",
        "fill_form",
        "submit_form",
        "click_link",
        "api_call",
        "webhook",
    })


# ─────────────────────────────────────────────────────────────────────────────
# HTTP method registry
# ─────────────────────────────────────────────────────────────────────────────

_SUPPORTED_HTTP_METHODS: frozenset[str] = frozenset({
    "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS",
})


# ─────────────────────────────────────────────────────────────────────────────
# Main tool class
# ─────────────────────────────────────────────────────────────────────────────

class APITool:
    """
    Structured HTTP execution tool.

    Registration
    ────────────
    tool_type = "api_tool"  → tool_registry assigns priority 90 (above
    shell and UI, below plugin).

    Singleton usage
    ───────────────
    Imported as `from tools.api_tool import api_tool` by tool_registry
    and by the router's _evaluate_api() fallback check.

    Session management
    ──────────────────
    _session is created lazily on first use and reused across calls.
    Call await api_tool.close() on agent shutdown to release connections.
    """

    name      : str = "api_tool"
    tool_type : str = "api_tool"
    _CATCH_ALL: bool = False   # not a catch-all; only handles known intents

    def __init__(self) -> None:
        self._supported_intents: frozenset[str] = _load_supported_intents()
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    # ── Intent interface (queried by MethodRouter and tool_registry) ──────────

    @property
    def supported_intents(self) -> frozenset[str]:
        """Live view — re-reads from settings so hot-config changes apply."""
        return _load_supported_intents()

    def can_handle(self, intent: str) -> float:
        """
        Return 1.0 if this tool handles *intent*, 0.0 otherwise.

        Returns float (not bool) so the router receives a confidence score
        consistent with the plugin evaluator's scoring interface.
        """
        return 1.0 if intent in self.supported_intents else 0.0

    # ── Session lifecycle ─────────────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Return the shared ClientSession, creating it if necessary.

        Uses a lock to prevent multiple concurrent calls from each
        creating their own session on first use.
        """
        async with self._session_lock:
            if self._session is None or self._session.closed:
                timeout_seconds: float = float(
                    getattr(settings, "API_TOOL_TIMEOUT_SECONDS", 15)
                )
                timeout = aiohttp.ClientTimeout(total=timeout_seconds)
                self._session = aiohttp.ClientSession(timeout=timeout)
                logger.debug("APITool: new ClientSession created (timeout=%.1fs)", timeout_seconds)
        return self._session

    async def close(self) -> None:
        """Release the shared ClientSession.  Call on agent shutdown."""
        async with self._session_lock:
            if self._session and not self._session.closed:
                await self._session.close()
                self._session = None
                logger.info("APITool: ClientSession closed.")

    # ── Core execution ────────────────────────────────────────────────────────

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Execute an HTTP request described by *args* (the api_body slot of
        LayeredPayload).

        Args dict keys
        ──────────────
        url         (str, required) — target URL
        method      (str, default "GET") — HTTP verb
        data        (dict, optional) — JSON request body (POST / PUT / PATCH)
        headers     (dict, optional) — additional HTTP headers
        params      (dict, optional) — URL query parameters
        _intent     (str)  — routing metadata; forwarded in log events
        _profile_hint (str | None) — forwarded for tracing

        Returns
        ───────
        {
            "success"     : bool,
            "status_code" : int | None,
            "data"        : dict | str | None,   # parsed JSON or raw text
            "intent"      : str,
            "url"         : str,
            "method"      : str,
        }

        Raises
        ──────
        APIPermanentError — 4xx responses (except 429), bad URL, missing url
        APITransientError — 5xx responses, 429, timeouts, connection errors
        """
        intent_str : str = str(args.get("_intent") or "")
        url        : str | None = args.get("url")
        http_method: str = str(args.get("method", "GET")).upper().strip()
        body       : dict = dict(args.get("data") or {})
        headers    : dict = dict(args.get("headers") or {})
        query_params: dict = dict(args.get("params") or {})

        # ── Validation ────────────────────────────────────────────────────────
        if not url:
            raise APIPermanentError(
                f"APITool: 'url' is required but was not provided "
                f"(intent='{intent_str}')",
                status_code=None,
            )

        if http_method not in _SUPPORTED_HTTP_METHODS:
            raise APIPermanentError(
                f"APITool: unsupported HTTP method '{http_method}' "
                f"(supported: {sorted(_SUPPORTED_HTTP_METHODS)})",
                status_code=None,
            )

        max_retries: int = int(getattr(settings, "MAX_RETRY_ATTEMPTS", 3))
        last_exc: Exception | None = None

        bus.publish(
            "api_op_started",
            {"url": url, "method": http_method, "intent": intent_str},
            source="api_tool",
        )

        for attempt in range(1, max_retries + 1):
            try:
                result = await self._execute_request(
                    url=url,
                    http_method=http_method,
                    body=body,
                    headers=headers,
                    query_params=query_params,
                    intent_str=intent_str,
                )
                bus.publish(
                    "api_op_success",
                    {
                        "url"        : url,
                        "method"     : http_method,
                        "intent"     : intent_str,
                        "status_code": result.get("status_code"),
                    },
                    source="api_tool",
                )
                return result

            except APITransientError as exc:
                last_exc = exc
                logger.warning(
                    "APITool: transient error on attempt %d/%d for %s %r — %s",
                    attempt, max_retries, http_method, url, exc,
                )
                if attempt < max_retries:
                    # Exponential back-off: 0.5s, 1s, 2s, …
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                continue

            except APIPermanentError:
                # Permanent errors should not be retried
                raise

        # All retries exhausted
        bus.publish(
            "api_op_failed",
            {"url": url, "method": http_method, "intent": intent_str,
             "error": str(last_exc)},
            source="api_tool",
        )
        raise last_exc  # type: ignore[misc]

    async def _execute_request(
        self,
        url          : str,
        http_method  : str,
        body         : dict,
        headers      : dict,
        query_params : dict,
        intent_str   : str,
    ) -> dict[str, Any]:
        """
        Issue a single HTTP request and parse the response.

        Classifies the response status into success / transient / permanent
        so the executor can tag FailureClass without inspecting HTTP status
        codes itself.
        """
        session = await self._get_session()
        request_kwargs: dict[str, Any] = {"headers": headers}

        if query_params:
            request_kwargs["params"] = query_params

        if http_method in {"POST", "PUT", "PATCH"} and body:
            request_kwargs["json"] = body

        try:
            async with session.request(
                method=http_method,
                url=url,
                **request_kwargs,
            ) as response:
                status = response.status
                return await self._parse_response(
                    response=response,
                    status=status,
                    url=url,
                    http_method=http_method,
                    intent_str=intent_str,
                )

        except aiohttp.ClientConnectorError as exc:
            raise APITransientError(
                f"Connection refused or DNS failure for {url!r}: {exc}",
                status_code=None,
            ) from exc
        except asyncio.TimeoutError as exc:
            raise APITransientError(
                f"Request timed out for {http_method} {url!r}",
                status_code=None,
            ) from exc
        except aiohttp.ClientError as exc:
            # Covers malformed URL, SSL errors, etc. — treat as permanent
            raise APIPermanentError(
                f"HTTP client error for {http_method} {url!r}: {exc}",
                status_code=None,
            ) from exc

    @staticmethod
    async def _parse_response(
        response   : aiohttp.ClientResponse,
        status     : int,
        url        : str,
        http_method: str,
        intent_str : str,
    ) -> dict[str, Any]:
        """
        Parse the response body and classify the status code.

        Status classification
        ─────────────────────
        2xx         → success
        3xx         → success (aiohttp follows redirects by default)
        429         → APITransientError (rate-limited; retry with back-off)
        4xx (other) → APIPermanentError (bad request, auth failure, not found)
        5xx         → APITransientError (server error; may recover)
        """
        # Try JSON first; fall back to text
        content_type = response.headers.get("Content-Type", "")
        data: Any
        if "application/json" in content_type:
            try:
                data = await response.json(content_type=None)
            except Exception:
                data = await response.text()
        else:
            data = await response.text()

        if 200 <= status < 300:
            return {
                "success"     : True,
                "status_code" : status,
                "data"        : data,
                "intent"      : intent_str,
                "url"         : url,
                "method"      : http_method,
            }

        if status == 429:
            raise APITransientError(
                f"Rate limited (HTTP 429) for {http_method} {url!r}. "
                f"Response: {data!r}",
                status_code=status,
            )

        if 400 <= status < 500:
            raise APIPermanentError(
                f"HTTP {status} for {http_method} {url!r}. Response: {data!r}",
                status_code=status,
            )

        if status >= 500:
            raise APITransientError(
                f"HTTP {status} (server error) for {http_method} {url!r}. "
                f"Response: {data!r}",
                status_code=status,
            )

        # Unexpected status (1xx, 3xx that weren't followed)
        return {
            "success"     : False,
            "status_code" : status,
            "data"        : data,
            "intent"      : intent_str,
            "url"         : url,
            "method"      : http_method,
        }

    # ── Sync wrapper (for non-async callers) ──────────────────────────────────

    def run_sync(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Blocking wrapper around run() for callers that cannot use async.

        Creates a fresh event loop if none is running.  Do NOT call this
        from within an async context — use `await run()` instead.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            raise RuntimeError(
                "APITool.run_sync() called from inside a running event loop. "
                "Use 'await api_tool.run(args)' instead."
            )

        return asyncio.run(self.run(args))


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

api_tool = APITool()