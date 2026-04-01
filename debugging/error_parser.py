import logging
import re
import traceback
from typing import Any, Dict


class ErrorParser:
    """🛠️ The Diagnostic Tool of the Self-Healing System.

    Takes raw error payloads or exceptions and parses them into a structured
    report without hardcoding specific error strings.
    """

    def __init__(self):
        self.logger = logging.getLogger("ErrorParser")

    def parse(self, error_payload: Any) -> Dict[str, Any]:
        """Analyzes the incoming error and extracts structured data.

        Handles dictionaries sent from the Event Bus, raw Exceptions, or string
        tracebacks.
        """
        report = {
            "error_type": "UnknownError",
            "message": "No message provided",
            "file": None,
            "line": None,
            "function": "unknown",
            "traceback": "",
        }

        try:
            # Case 1: The payload is a dictionary (from our Event Bus / ErrorHandler)
            if isinstance(error_payload, dict):
                report["error_type"] = error_payload.get(
                    "type", report["error_type"]
                )
                report["message"] = error_payload.get(
                    "message", report["message"]
                )
                report["traceback"] = error_payload.get("traceback", "")

                # If a traceback string was included, let's extract the file and line from it!
                if report["traceback"]:
                    self._extract_from_traceback_string(report)

            # Case 2: The payload is an actual Python Exception object
            elif isinstance(error_payload, Exception):
                report["error_type"] = type(error_payload).__name__
                report["message"] = str(error_payload)

                # Dynamically extract the traceback frames using Python's traceback module
                tb = error_payload.__traceback__
                if tb:
                    # Get the very last frame (where the error actually occurred)
                    summary = traceback.extract_tb(tb)[-1]
                    report["file"] = summary.filename
                    report["line"] = summary.lineno
                    report["function"] = summary.name
                    report["traceback"] = "".join(
                        traceback.format_exception(
                            type(error_payload), error_payload, tb
                        )
                    )

            # Case 3: It's just a raw string
            elif isinstance(error_payload, str):
                report["message"] = error_payload
                report["traceback"] = error_payload
                self._extract_from_traceback_string(report)

        except Exception as e:
            self.logger.error(f"Failed to parse error payload: {e}")

        return report

    