"""
Datetime Compatibility Module
────────────────────────────

Provides UTC timezone compatibility for Python < 3.11
"""
from datetime import timezone

# Compatibility for Python < 3.11
try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc
