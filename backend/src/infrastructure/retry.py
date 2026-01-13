"""Retry utilities using tenacity for resilient operations."""

import logging
from collections.abc import Callable
from functools import wraps
from typing import TypeVar

from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Default retry configuration
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_INITIAL_WAIT = 2.0  # seconds
DEFAULT_MAX_WAIT = 30.0  # seconds
DEFAULT_JITTER = 1.0  # seconds


class RetryableError(Exception):
    """Base class for errors that should trigger retries."""

    pass


class TransientError(RetryableError):
    """Network timeouts, temporary unavailability."""

    pass


class PermanentError(Exception):
    """Errors that should NOT be retried (auth failures, invalid data)."""

    pass


def with_retry(
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_wait: float = DEFAULT_INITIAL_WAIT,
    max_wait: float = DEFAULT_MAX_WAIT,
    retryable_exceptions: tuple = (RetryableError, ConnectionError, TimeoutError),
) -> Callable:
    """Decorator for async functions with exponential backoff retry.

    Uses exponential backoff with jitter to prevent synchronized retry storms.

    Wait formula: min(initial * 2^n + random(0, jitter), max)

    Example timeline with 2s initial:
        Attempt 1: immediate
        Attempt 2: ~2-3s wait
        Attempt 3: ~4-5s wait

    Args:
        max_attempts: Maximum number of attempts (default 3)
        initial_wait: Initial wait time in seconds (default 2.0)
        max_wait: Maximum wait time in seconds (default 30.0)
        retryable_exceptions: Tuple of exception types to retry on

    Returns:
        Decorated async function with retry behavior
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(max_attempts),
                wait=wait_exponential_jitter(
                    initial=initial_wait,
                    max=max_wait,
                    jitter=DEFAULT_JITTER,
                ),
                retry=retry_if_exception_type(retryable_exceptions),
                reraise=True,
            ):
                with attempt:
                    attempt_num = attempt.retry_state.attempt_number
                    if attempt_num > 1:
                        logger.warning(
                            f"Retry attempt {attempt_num}/{max_attempts} for {func.__name__}"
                        )
                    return await func(*args, **kwargs)

        return wrapper

    return decorator


async def retry_operation(
    operation: Callable[..., T],
    *args,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_wait: float = DEFAULT_INITIAL_WAIT,
    max_wait: float = DEFAULT_MAX_WAIT,
    retryable_exceptions: tuple = (RetryableError, ConnectionError, TimeoutError),
    on_retry: Callable[[int, Exception], None] | None = None,
    **kwargs,
) -> T:
    """Execute an async operation with retry logic.

    This is a functional alternative to the decorator for cases where
    you need more control over the retry behavior.

    Args:
        operation: Async function to execute
        *args: Positional arguments for operation
        max_attempts: Maximum number of attempts
        initial_wait: Initial wait time in seconds
        max_wait: Maximum wait time in seconds
        retryable_exceptions: Exception types to retry on
        on_retry: Optional callback called on each retry with (attempt, exception)
        **kwargs: Keyword arguments for operation

    Returns:
        Result of the operation

    Raises:
        RetryError: If all retry attempts failed
        Exception: The last exception if it's not retryable
    """
    last_exception: Exception | None = None

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(
            initial=initial_wait,
            max=max_wait,
            jitter=DEFAULT_JITTER,
        ),
        retry=retry_if_exception_type(retryable_exceptions),
        reraise=True,
    ):
        with attempt:
            attempt_num = attempt.retry_state.attempt_number
            if attempt_num > 1 and on_retry and last_exception:
                on_retry(attempt_num, last_exception)
            try:
                return await operation(*args, **kwargs)
            except Exception as e:
                last_exception = e
                raise

    # This should not be reached due to reraise=True, but just in case
    raise RetryError(last_exception) if last_exception else RuntimeError("No attempts made")
