import asyncio
import time
from collections.abc import Callable
from typing import Any

import httpx


class YouTubeConfigurationError(Exception):
    pass


class YouTubeQuotaExceededError(Exception):
    pass


class YouTubeAPIUnavailableError(Exception):
    pass


_QUOTA_REASONS = {
    "dailyLimitExceeded",
    "quotaExceeded",
    "rateLimitExceeded",
    "userRateLimitExceeded",
}


def _error_reasons(payload: Any) -> set[str]:
    error = payload.get("error") if isinstance(payload, dict) else None
    errors = error.get("errors") if isinstance(error, dict) else None
    if not isinstance(errors, list):
        return set()
    return {
        str(item.get("reason"))
        for item in errors
        if isinstance(item, dict) and item.get("reason")
    }


class YouTubeSearchClient:
    SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str | None,
        *,
        max_results: int = 5,
        daily_budget: int = 80,
        budget_period_seconds: float = 86_400.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.http = http
        self.api_key = api_key
        self.max_results = max(1, min(max_results, 5))
        self.daily_budget = max(1, daily_budget)
        self.budget_period_seconds = max(1.0, budget_period_seconds)
        self._clock = clock
        self._budget_started_at = clock()
        self._budget_used = 0
        self._budget_lock = asyncio.Lock()

    async def _reserve_search(self) -> None:
        async with self._budget_lock:
            now = self._clock()
            if now - self._budget_started_at >= self.budget_period_seconds:
                self._budget_started_at = now
                self._budget_used = 0
            if self._budget_used >= self.daily_budget:
                raise YouTubeQuotaExceededError("Local YouTube search budget exceeded")
            self._budget_used += 1

    async def search(self, name: str, artist: str) -> list[dict[str, Any]]:
        if not self.api_key:
            raise YouTubeConfigurationError("YOUTUBE_API_KEY is not configured")
        await self._reserve_search()

        try:
            response = await self.http.get(
                self.SEARCH_URL,
                params={
                    "part": "snippet",
                    "type": "video",
                    "videoCategoryId": "10",
                    "regionCode": "KR",
                    "relevanceLanguage": "ko",
                    "maxResults": self.max_results,
                    "q": f"{artist} {name} official audio".strip(),
                    "key": self.api_key,
                },
            )
        except httpx.HTTPError as exc:
            raise YouTubeAPIUnavailableError("YouTube search request failed") from exc

        if response.status_code == 429:
            raise YouTubeQuotaExceededError("YouTube search quota exceeded")

        try:
            payload = response.json()
        except Exception as exc:
            raise YouTubeAPIUnavailableError(
                "YouTube search returned invalid JSON"
            ) from exc
        if response.status_code >= 400:
            if _error_reasons(payload) & _QUOTA_REASONS:
                raise YouTubeQuotaExceededError("YouTube search quota exceeded")
            raise YouTubeAPIUnavailableError(
                f"YouTube search returned HTTP {response.status_code}"
            )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise YouTubeAPIUnavailableError(
                "YouTube search response did not contain items"
            )
        return [item for item in items if isinstance(item, dict)]
