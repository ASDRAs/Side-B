import asyncio
import threading
import time
from collections import OrderedDict
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


def test_cache_evicts_oldest_entries_beyond_limit(monkeypatch):
    """상한이 없으면 캐시가 프로세스 수명 내내 증가한다."""
    monkeypatch.setattr(sources, "_CACHE_MAX_ENTRIES", 3)
    monkeypatch.setattr(sources, "_cache", OrderedDict())

    for index in range(5):
        sources._cache_set(f"key-{index}", index)

    assert list(sources._cache) == ["key-2", "key-3", "key-4"]


def test_cache_removes_expired_entry_on_read(monkeypatch):
    """만료 항목을 읽기만 거부하면 메모리에 계속 남는다."""
    monkeypatch.setattr(sources, "_cache", OrderedDict())
    sources._cache_set("expiring", "value")

    hit, cached = sources._cache_get("expiring", ttl=0.0)

    assert hit is False
    assert cached is None
    assert "expiring" not in sources._cache


async def test_lastfm_call_limits_concurrent_threads(monkeypatch):
    """pylast는 to_thread로 돌기 때문에 상한이 없으면 기본 스레드풀을 점유한다."""
    monkeypatch.setattr(sources, "_LASTFM_SEMAPHORE", asyncio.Semaphore(2))
    monkeypatch.setattr(sources, "_cache", OrderedDict())

    lock = threading.Lock()
    state = {"active": 0, "max_active": 0}

    def blocking_call(index):
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.01)
        with lock:
            state["active"] -= 1
        return index

    await asyncio.gather(
        *[
            sources._lf_call(f"lf:concurrency:{index}", 600, blocking_call, index)
            for index in range(8)
        ]
    )

    assert state["max_active"] <= 2


async def test_lastfm_call_times_out_instead_of_hanging(monkeypatch):
    """pylast 호출에 자체 데드라인이 없어 행이 걸리면 요청이 무한 대기한다."""
    monkeypatch.setattr(sources, "_LASTFM_CALL_TIMEOUT", 0.05)
    monkeypatch.setattr(sources, "_cache", OrderedDict())

    def hanging_call():
        time.sleep(0.3)
        return "too late"

    with pytest.raises(asyncio.TimeoutError):
        await sources._lf_call("lf:timeout", 600, hanging_call)

    assert "lf:timeout" not in sources._cache


def test_artist_top_cache_keys_differ_by_limit():
    artist = "radiohead"
    assert f"lf:artist_top:{artist}:20" != f"lf:artist_top:{artist}:4"


def test_itunes_cache_keys_differ_by_min_score():
    term = "some track"
    limit = 5
    assert f"itunes:{term}:{limit}:0.35" != f"itunes:{term}:{limit}:0.65"
