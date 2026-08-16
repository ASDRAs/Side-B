import asyncio
import threading
import time
from collections import OrderedDict
from unittest.mock import AsyncMock

import pylast
import pytest

from app.services.catalog import (
    CatalogClient,
    DeezerRateLimitError,
    ItunesRateLimitError,
)
from recommend_algo import similar_listening_pattern
from recommend_algo.common import sources
from tests.test_api_call_counts import EmptyHttp


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


async def test_lastfm_rate_limit_opens_circuit_and_skips_followup_call(monkeypatch):
    """제한을 받고도 계속 두드리면 차단이 길어진다. iTunes·Deezer와 같은 규칙."""
    monkeypatch.setattr(sources, "_cache", OrderedDict())
    calls = []

    def rate_limited_call():
        calls.append(1)
        raise pylast.WSError(
            None,
            str(pylast.STATUS_RATE_LIMIT_EXCEEDED),
            "Rate limit exceeded",
        )

    with pytest.raises(sources.LastfmRateLimitError):
        await sources._lf_call("lf:limited:1", 600, rate_limited_call)
    assert sources._is_lf_rate_limited()

    # 차단 중에는 pylast를 아예 부르지 않는다.
    with pytest.raises(sources.LastfmRateLimitError):
        await sources._lf_call("lf:limited:2", 600, rate_limited_call)
    assert len(calls) == 1


async def test_other_lastfm_errors_do_not_open_the_circuit(monkeypatch):
    """제한이 아닌 오류까지 차단으로 다루면 곡 하나 없는 응답이 60초를 막는다."""
    monkeypatch.setattr(sources, "_cache", OrderedDict())

    def missing_resource():
        raise pylast.WSError(None, str(pylast.STATUS_INVALID_RESOURCE), "not found")

    with pytest.raises(pylast.WSError):
        await sources._lf_call("lf:other-error", 600, missing_resource)
    assert not sources._is_lf_rate_limited()


async def test_open_lastfm_circuit_empties_a_bucket_instead_of_failing(monkeypatch):
    """차단 중에는 버킷이 비는 것이 맞는 동작이다. 500이 되면 안 된다."""
    monkeypatch.setattr(sources, "_cache", OrderedDict())
    sources._mark_lf_rate_limited(60)

    class ExplodingLastFm:
        def get_artist(self, name):
            raise AssertionError("차단 중에 Last.fm을 불렀다")

        def get_track(self, artist, name):
            raise AssertionError("차단 중에 Last.fm을 불렀다")

    result = await similar_listening_pattern(
        "Seed", "Artist", EmptyHttp(), ExplodingLastFm(), top_n=10
    )

    assert result == []


async def test_bursts_within_the_window_budget_are_not_delayed(monkeypatch):
    """ToS는 초당 5회를 5분 평균으로 요구한다. 봉우리 자체는 위반이 아니다.

    매 호출을 0.2초로 벌리면 실측 direct 1건의 응답이 4.8초에서 11.5초가 된다.
    규정이 요구하지 않는 지연이므로 예산이 남아 있는 동안은 기다리지 않는다.
    """
    monkeypatch.setattr(sources, "_cache", OrderedDict())
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    for index in range(50):
        await sources._lf_call(f"lf:burst:{index}", 600, lambda i=index: i)

    assert delays == []
    assert len(sources._LASTFM_SENT) == 50


async def test_spent_window_budget_makes_the_next_call_wait(monkeypatch):
    """창이 가득 차면 가장 오래된 호출이 빠져나갈 때까지 기다린다."""
    monkeypatch.setattr(sources, "_cache", OrderedDict())
    monkeypatch.setattr(sources, "_LASTFM_MAX_PER_WINDOW", 3)
    monkeypatch.setattr(sources, "_LASTFM_RATE_WINDOW", 10.0)
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)
        # 잠든 사이에 창이 지나간 것으로 만든다. 그러지 않으면 예산이 영원히
        # 차 있어 무한 루프가 된다.
        sources._LASTFM_SENT.clear()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    for index in range(4):
        await sources._lf_call(f"lf:budget:{index}", 600, lambda i=index: i)

    assert len(delays) == 1
    assert 0 < delays[0] <= 10.0


async def test_cached_lastfm_reads_do_not_consume_budget(monkeypatch):
    """캐시 적중은 공급자를 부르지 않으므로 예산과 무관하다."""
    monkeypatch.setattr(sources, "_cache", OrderedDict())

    for _ in range(5):
        await sources._lf_call("lf:same-key", 600, lambda: "value")

    assert len(sources._LASTFM_SENT) == 1


def test_artist_top_cache_keys_differ_by_limit():
    artist = "radiohead"
    assert f"lf:artist_top:{artist}:20" != f"lf:artist_top:{artist}:4"


def test_itunes_cache_keys_differ_by_min_score():
    term = "some track"
    limit = 5
    assert f"itunes:{term}:{limit}:0.35" != f"itunes:{term}:{limit}:0.65"
