"""
Async Helper Utilities — Operonix Graph
────────────────────────────────────

Helper functions for safely running async operations from sync context.
Used by graph nodes which are synchronous but need to call async services.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, TypeVar, Optional

logger = logging.getLogger("Graph.AsyncHelpers")

T = TypeVar('T')


def run_async_safely(
    coro: Callable[..., Any],
    *args: Any,
    timeout: Optional[float] = 30.0,
    **kwargs: Any
) -> Any:
    """Safely run an async coroutine from sync context.
    
    This function wraps asyncio.run() with proper error handling and timeout.
    It's designed for use in synchronous graph nodes that need to call async services.
    
    Args:
        coro: Async coroutine function to execute
        *args: Positional arguments to pass to the coroutine
        timeout: Timeout in seconds (None for no timeout)
        **kwargs: Keyword arguments to pass to the coroutine
        
    Returns:
        Result from the coroutine
        
    Raises:
        Exception: If the coroutine fails or times out
    """
    try:
        # Create the coroutine
        async_func = coro(*args, **kwargs)
        
        # Run with timeout if specified
        if timeout is not None:
            result = asyncio.run(asyncio.wait_for(async_func, timeout=timeout))
        else:
            result = asyncio.run(async_func)
        
        return result
        
    except asyncio.TimeoutError:
        logger.error(f"Async operation timed out after {timeout}s")
        raise TimeoutError(f"Async operation timed out after {timeout}s")
    except RuntimeError as e:
        # This can happen if there's already a running event loop
        # In the graph context, this shouldn't happen, but we handle it gracefully
        logger.error(f"RuntimeError in async operation: {e}")
        raise RuntimeError(f"Async operation failed: {e}")
    except Exception as e:
        logger.error(f"Async operation failed: {e}")
        raise


def run_async_with_fallback(
    coro: Callable[..., Any],
    fallback_value: Any,
    *args: Any,
    timeout: Optional[float] = 30.0,
    **kwargs: Any
) -> Any:
    """Run async coroutine with fallback value on error.
    
    This is useful when you want to attempt an async operation but fall back
    to a default value if it fails.
    
    Args:
        coro: Async coroutine function to execute
        fallback_value: Value to return if the coroutine fails
        *args: Positional arguments to pass to the coroutine
        timeout: Timeout in seconds (None for no timeout)
        **kwargs: Keyword arguments to pass to the coroutine
        
    Returns:
        Result from the coroutine, or fallback_value on error
    """
    try:
        return run_async_safely(coro, *args, timeout=timeout, **kwargs)
    except Exception as e:
        logger.warning(f"Async operation failed, using fallback: {e}")
        return fallback_value
