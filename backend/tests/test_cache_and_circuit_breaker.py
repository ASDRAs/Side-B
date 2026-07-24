import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services.catalog import (
    CatalogClient,
    DeezerRateLimitError,
    ItunesRateLimitError,
)
from recommend_algo.common import sources


async def test_deezer_429_propagates_rate_limit_error():
    mock_response = AsyncMock()
    mock_response.status_code = 429
    mock_response.headers = {"Retry-After": "30"}

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=mock_response)

    catalog = CatalogClient(mock_http)
    with pytest.raises(DeezerRateLimitError) as exc_info:
        await catalog.deezer_search_best("some track", "some artist")
    assert exc_info.value.retry_after == 30


async def test_itunes_429_propagates_rate_limit_error():
    mock_response = AsyncMock()
    mock_response.status_code = 429
    mock_response.headers = {"Retry-After": "45"}

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=mock_response)

    catalog = CatalogClient(mock_http)
    with pytest.raises(ItunesRateLimitError) as exc_info:
        await catalog.itunes_search_best("some track", "some artist")
    assert exc_info.value.retry_after == 45


async def test_itunes_search_limits_concurrent_requests():
    class EmptyResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    class CountingHttp:
        def __init__(self):
            self.active = 0
            self.max_active = 0

        async def get(self, *args, **kwargs):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return EmptyResponse()

    http = CountingHttp()
    await asyncio.gather(
        *[
            sources._itunes_search(
                http,
                f"concurrency-test-{index}",
                "artist",
                limit=5,
            )
            for index in range(16)
        ]
    )

    assert http.max_active <= 8


async def test_itunes_429_opens_circuit_and_skips_followup_request(monkeypatch):
    class RateLimitedResponse:
        status_code = 429
        headers = {"Retry-After": "30"}

        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    class CountingHttp:
        def __init__(self):
            self.calls = 0

        async def get(self, *args, **kwargs):
            self.calls += 1
            return RateLimitedResponse()

    http = CountingHttp()
    monkeypatch.setattr(sources, "_ITUNES_RATE_LIMIT_UNTIL", 0.0)

    first = await sources._itunes_or_none(
        http,
        "rate-limit-first",
        "artist",
    )
    second = await sources._itunes_or_none(
        http,
        "rate-limit-second",
        "artist",
    )

    assert first is None
    assert second is None
    assert http.calls == 1


def test_artist_top_cache_keys_differ_by_limit():
    artist = "radiohead"
    assert f"lf:artist_top:{artist}:20" != f"lf:artist_top:{artist}:4"


def test_itunes_cache_keys_differ_by_min_score():
    term = "some track"
    limit = 5
    assert f"itunes:{term}:{limit}:0.35" != f"itunes:{term}:{limit}:0.65"
