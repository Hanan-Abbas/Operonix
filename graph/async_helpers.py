"""
Async Helper Utilities — Operonix Graph
────────────────────────────────────

Helper functions for safely running async operations from sync context.
Used by graph nodes which are synchronous but need to call async services.

IMPORTANT: The graph runs in a separate thread (via runtime_adapter.run_in_executor)
to avoid event loop conflicts with the main async loop. This isolation allows
graph nodes to use asyncio.run() safely. If you encounter "running event loop"
errors, check that runtime_adapter._execute_with_graph is using run_in_executor.
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
    
    This function is designed for use in synchronous graph nodes that need to
    call async services. Since the graph now runs in a separate thread (via
    runtime_adapter.run_in_executor), there should be no running event loop
    in the thread context, making asyncio.run() safe to use.
    
    If a running event loop is detected (e.g., if the architecture changes),
    this function will raise a clear error to guide the fix.
    
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
        # Check if there's already a running event loop
        # This should not happen given the thread isolation, but we check defensively
        try:
            loop = asyncio.get_running_loop()
            # If we get here, there's a running loop - this is an architectural issue
            logger.error(
                "Detected running event loop in graph thread context. "
                "This indicates the graph is not properly isolated. "
                "Ensure runtime_adapter.run_in_executor is being used."
            )
            raise RuntimeError(
                "Cannot call asyncio.run() from running event loop. "
                "Graph should run in a separate thread to avoid this. "
                "Check runtime_adapter._execute_with_graph implementation."
            )
        except RuntimeError:
            # No running loop - this is the expected case
            # Safe to use asyncio.run()
            pass
        
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
        # This captures both the loop detection error and any other RuntimeError
        logger.error(f"RuntimeError in async operation: {e}")
        raise
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
