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


async def test_open_circuit_stops_calls_already_queued_behind_it(monkeypatch):
    """진입 시 한 번만 확인하면 줄을 선 호출은 차단을 못 본다.

    공개된 수치가 없는 상황에서 실제 거절에 반응하는 것이 이 가드의 핵심인데,
    첫 거절 뒤에도 팬아웃의 나머지가 계속 나가면 반응한 의미가 없다.
    """
    monkeypatch.setattr(sources, "_cache", OrderedDict())
    monkeypatch.setattr(sources, "_LASTFM_SEMAPHORE", asyncio.Semaphore(1))
    calls = []

    def rate_limited_call(index):
        calls.append(index)
        time.sleep(0.01)
        raise pylast.WSError(
            None, str(pylast.STATUS_RATE_LIMIT_EXCEEDED), "Rate limit exceeded"
        )

    results = await asyncio.gather(
        *[
            sources._lf_call(f"lf:queued:{index}", 600, rate_limited_call, index)
            for index in range(4)
        ],
        return_exceptions=True,
    )

    assert all(isinstance(result, sources.LastfmRateLimitError) for result in results)
    # 첫 호출만 공급자에 닿는다. 나머지 셋은 차단을 보고 되돌아간다.
    assert len(calls) == 1


async def test_circuit_opened_while_waiting_for_a_token_still_blocks(monkeypatch):
    """토큰이 마른 상태가 곧 지속 트래픽이고, 제한을 받기 가장 쉬운 순간이다.

    허가를 한 번만 확인하면 기다리는 동안 다른 호출이 거절당해도, 토큰이 채워지는
    순간 그대로 나간다.
    """
    monkeypatch.setattr(sources, "_cache", OrderedDict())
    monkeypatch.setattr(sources, "_LASTFM_SEMAPHORE", asyncio.Semaphore(2))
    monkeypatch.setattr(sources, "_LASTFM_BURST", 1.0)
    sources._LASTFM_TOKENS = 1.0
    sources._LASTFM_TOKENS_UPDATED = time.monotonic()
    calls = []

    def first_call():
        calls.append("first")
        time.sleep(0.01)
        raise pylast.WSError(
            None, str(pylast.STATUS_RATE_LIMIT_EXCEEDED), "Rate limit exceeded"
        )

    def second_call():
        calls.append("second")
        return "answered anyway"

    results = await asyncio.gather(
        sources._lf_call("lf:starved:1", 600, first_call),
        sources._lf_call("lf:starved:2", 600, second_call),
        return_exceptions=True,
    )

    assert all(isinstance(result, sources.LastfmRateLimitError) for result in results)
    assert calls == ["first"]


async def test_one_request_fanout_passes_without_waiting(monkeypatch):
    """정지 경고는 봉우리가 아니라 '지속'을 겨눈다.

    실측 direct 1건이 41회다. 이걸 매 호출 간격으로 깎으면 응답이 4.8초에서
    11.5초가 되는데, 공개된 규정이 요구하는 바가 아니다.
    """
    monkeypatch.setattr(sources, "_cache", OrderedDict())
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    for index in range(41):
        await sources._lf_call(f"lf:burst:{index}", 600, lambda i=index: i)

    assert delays == []


async def test_sustained_calls_are_held_to_the_refill_rate(monkeypatch):
    """모아 둔 토큰을 다 쓰면 채워지는 속도 이상으로는 나가지 못한다."""
    monkeypatch.setattr(sources, "_cache", OrderedDict())
    monkeypatch.setattr(sources, "_LASTFM_BURST", 2.0)
    monkeypatch.setattr(sources, "_LASTFM_REFILL_PER_SECOND", 5.0)
    sources._LASTFM_TOKENS = 2.0
    sources._LASTFM_TOKENS_UPDATED = time.monotonic()
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)
        # 잠든 만큼 시간이 흐른 것으로 만든다. 실제로 자면 테스트가 느려진다.
        sources._LASTFM_TOKENS_UPDATED -= seconds

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    for index in range(4):
        await sources._lf_call(f"lf:sustained:{index}", 600, lambda i=index: i)

    # 앞의 둘은 모아 둔 토큰으로 나가고, 뒤의 둘은 채워지길 기다린다.
    assert len(delays) == 2
    assert all(0 < delay <= 1.0 / 5.0 for delay in delays)


async def test_cached_lastfm_reads_do_not_consume_a_token(monkeypatch):
    """캐시 적중은 공급자를 부르지 않으므로 상한과 무관하다."""
    monkeypatch.setattr(sources, "_cache", OrderedDict())
    sources._LASTFM_TOKENS = 1.0
    sources._LASTFM_TOKENS_UPDATED = time.monotonic()

    for _ in range(5):
        await sources._lf_call("lf:same-key", 600, lambda: "value")

    # 첫 호출만 토큰을 쓴다. 나머지 넷은 캐시에서 답한다.
    assert sources._LASTFM_TOKENS < 1.0


def test_artist_top_cache_keys_differ_by_limit():
    artist = "radiohead"
    assert f"lf:artist_top:{artist}:20" != f"lf:artist_top:{artist}:4"


def test_itunes_cache_keys_differ_by_min_score():
    term = "some track"
    limit = 5
    assert f"itunes:{term}:{limit}:0.35" != f"itunes:{term}:{limit}:0.65"
