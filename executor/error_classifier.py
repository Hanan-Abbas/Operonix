# executor/error_classifier.py
"""
🛡️ Smart error classification with fallback patterns.
Prevents crashes when LLM is unavailable.
"""

import asyncio
import json
import logging
import re
from typing import Optional
from brain.llm_client import llm_client

logger = logging.getLogger("ErrorClassifier")


class ErrorClassifier:
    """Classify errors smartly with multiple fallback strategies."""
    
    # Pattern-based error categorization (FAST, NO LLM)
    FALLBACK_PATTERNS = {
        "permission_denied": re.compile(
            r"(permission denied|access denied|forbidden|not allowed|operation not permitted|"
            r"user is not in sudoers|sudo: .* command not found)",
            re.IGNORECASE
        ),
        "not_found": re.compile(
            r"(no such file|file not found|does not exist|not found|cannot find|"
            r"cannot open|404|path does not exist)",
            re.IGNORECASE
        ),
        "timeout": re.compile(
            r"(timeout|timed out|deadline exceeded|took too long|connection timeout|"
            r"read timeout|write timeout)",
            re.IGNORECASE
        ),
        "network_error": re.compile(
            r"(connection refused|connection reset|network unreachable|no route to host|"
            r"offline|cannot connect|broken pipe|connection lost)",
            re.IGNORECASE
        ),
        "resource_exhausted": re.compile(
            r"(out of memory|no space left|resource busy|too many open files|"
            r"memory allocation failed|oom|disk full)",
            re.IGNORECASE
        ),
        "invalid_input": re.compile(
            r"(invalid argument|bad request|syntax error|parse error|"
            r"malformed|invalid option|unrecognized|wrong number of arguments)",
            re.IGNORECASE
        ),
    }
    
    def __init__(self):
        self.logger = logging.getLogger("ErrorClassifier")
        self.llm_available = True
    
    async def classify(self, error_message: str, timeout_seconds: float = 2.0) -> str:
        """
        Classify error with priority:
        1. Fast regex patterns
        2. LLM (with timeout)
        3. Default: unknown_error
        
        Args:
            error_message: The error string to classify
            timeout_seconds: Max time to wait for LLM response
        
        Returns:
            Error category string (permission_denied, not_found, timeout, etc.)
        """
        
        error_lower = (error_message or "").lower().strip()
        if not error_lower:
            return "unknown_error"
        
        # ✅ FAST FALLBACK: Regex-based classification
        category = self._classify_by_patterns(error_lower)
        if category != "unknown_error":
            self.logger.debug(f"Error classified as '{category}' (regex pattern match)")
            return category
        
        # ✅ MEDIUM FALLBACK: Try LLM with timeout
        if self.llm_available:
            category = await self._classify_by_llm(error_message, timeout_seconds)
            if category != "unknown_error":
                self.logger.debug(f"Error classified as '{category}' (LLM)")
                return category
            else:
                self.logger.warning("LLM returned unknown_error or failed")
        
        # ✅ FINAL FALLBACK: Default to unknown
        self.logger.warning(f"Error classification failed: '{error_message[:100]}...'")
        return "unknown_error"
    
    def _classify_by_patterns(self, error_lower: str) -> str:
        """Fast regex-based error classification."""
        
        for category, pattern in self.FALLBACK_PATTERNS.items():
            if pattern.search(error_lower):
                return category
        
        return "unknown_error"
    
    async def _classify_by_llm(
        self, 
        error_message: str, 
        timeout_seconds: float
    ) -> str:
        """Classify using LLM with timeout guard."""
        
        prompt = f"""Classify this error into ONE category:
- permission_denied (access/auth issue)
- not_found (missing file/resource)
- timeout (operation took too long)
- network_error (connection problem)
- resource_exhausted (memory/disk full)
- invalid_input (bad parameters)
- unknown_error (doesn't fit above)

Error: "{error_message}"

Return ONLY valid JSON: {{"category": "<category_name>"}}"""
        
        try:
            # Call LLM with timeout
            response = await asyncio.wait_for(
                llm_client.generate(prompt, use_json=True),
                timeout=timeout_seconds
            )
            
            # Safely parse JSON
            if isinstance(response, dict):
                category = response.get("category", "unknown_error")
            else:
                try:
                    data = json.loads(str(response))
                    category = data.get("category", "unknown_error")
                except json.JSONDecodeError:
                    category = "unknown_error"
            
            # Validate category
            valid_categories = set(self.FALLBACK_PATTERNS.keys()) | {"unknown_error"}
            if category not in valid_categories:
                self.logger.warning(f"LLM returned invalid category '{category}'")
                return "unknown_error"
            
            return category
            
        except asyncio.TimeoutError:
            self.logger.warning(f"LLM classification timed out after {timeout_seconds}s")
            return "unknown_error"
        
        except Exception as e:
            self.logger.warning(f"LLM classification failed: {e}")
            # Disable LLM for future attempts if it keeps failing
            self.llm_available = False
            return "unknown_error"
    
    async def get_retry_strategy(self, category: str) -> dict:
        """Suggest retry strategy based on error category."""
        
        strategies = {
            "permission_denied": {
                "should_retry": False,
                "reason": "Permission denied - cannot retry",
                "suggestion": "Check user privileges or file permissions"
            },
            "not_found": {
                "should_retry": False,
                "reason": "Resource not found - will not appear on retry",
                "suggestion": "Verify the path or resource exists"
            },
            "timeout": {
                "should_retry": True,
                "reason": "Timeout - may succeed on retry",
                "suggestion": "Retry with increased timeout or reduced payload",
                "backoff_ms": 1000
            },
            "network_error": {
                "should_retry": True,
                "reason": "Network issue - may be transient",
                "suggestion": "Check network connectivity and retry",
                "backoff_ms": 2000
            },
            "resource_exhausted": {
                "should_retry": True,
                "reason": "Resource temporarily exhausted",
                "suggestion": "Retry after freeing resources",
                "backoff_ms": 5000
            },
            "invalid_input": {
                "should_retry": False,
                "reason": "Invalid input - will not change on retry",
                "suggestion": "Fix the input parameters"
            },
            "unknown_error": {
                "should_retry": True,
                "reason": "Unknown error - may be transient",
                "suggestion": "Retry with exponential backoff",
                "backoff_ms": 1000
            }
        }
        
        return strategies.get(category, strategies["unknown_error"])


# Global instance
error_classifier = ErrorClassifier()

