import asyncio
import math
import secrets
import time
from collections import deque
from collections.abc import Callable


class BackendAccessConfigurationError(Exception):
    pass


class BackendAccessUnauthorizedError(Exception):
    pass


class BackendAccessRateLimitError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Backend request rate exceeded")
        self.retry_after = max(1, retry_after)


class BackendAccess:
    """Authenticate protected backend requests and apply an instance-local limit."""

    def __init__(
        self,
        token: str | None,
        *,
        requests_per_minute: int = 6,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token = str(token or "").strip()
        self._request_limit = max(1, requests_per_minute)
        self._window_seconds = max(1.0, window_seconds)
        self._clock = clock
        self._requests: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def authorize(self, provided_token: str | None) -> None:
        if not self._token:
            raise BackendAccessConfigurationError(
                "SIDE_B_ACCESS_TOKEN is not configured"
            )
        candidate = str(provided_token or "").strip()
        if not candidate or not secrets.compare_digest(candidate, self._token):
            raise BackendAccessUnauthorizedError("Invalid backend access token")

        async with self._lock:
            now = self._clock()
            cutoff = now - self._window_seconds
            while self._requests and self._requests[0] <= cutoff:
                self._requests.popleft()
            if len(self._requests) >= self._request_limit:
                retry_after = math.ceil(
                    self._window_seconds - (now - self._requests[0])
                )
                raise BackendAccessRateLimitError(retry_after)
            self._requests.append(now)
