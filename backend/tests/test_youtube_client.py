import httpx
import pytest

from app.services.youtube.client import (
    YouTubeAPIUnavailableError,
    YouTubeConfigurationError,
    YouTubeQuotaExceededError,
    YouTubeSearchClient,
)


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload if payload is not None else {"items": []}
        self.json_calls = 0

    def json(self):
        self.json_calls += 1
        return self.payload


class _HTTP:
    def __init__(self, response=None, error=None):
        self.response = response or _Response()
        self.error = error
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


async def test_search_uses_one_music_video_query_with_bounded_results():
    response = _Response(payload={"items": [{"id": {"videoId": "id"}}]})
    http = _HTTP(response)
    client = YouTubeSearchClient(http, "secret-key", max_results=20)

    items = await client.search("혜성", "윤하")

    assert len(items) == 1
    assert len(http.calls) == 1
    assert response.json_calls == 1
    url, kwargs = http.calls[0]
    assert url.endswith("/search")
    assert kwargs["params"] == {
        "part": "snippet",
        "type": "video",
        "videoCategoryId": "10",
        "regionCode": "KR",
        "relevanceLanguage": "ko",
        "maxResults": 5,
        "q": "윤하 혜성 official audio",
        "key": "secret-key",
    }


async def test_search_rejects_missing_api_key_before_http_call():
    http = _HTTP()
    client = YouTubeSearchClient(http, None)

    with pytest.raises(YouTubeConfigurationError):
        await client.search("Hello", "Adele")

    assert http.calls == []


async def test_search_maps_quota_reason_and_429_separately():
    quota_response = _Response(
        403,
        {"error": {"errors": [{"reason": "quotaExceeded"}]}},
    )
    with pytest.raises(YouTubeQuotaExceededError):
        await YouTubeSearchClient(_HTTP(quota_response), "key").search("A", "B")

    with pytest.raises(YouTubeQuotaExceededError):
        await YouTubeSearchClient(_HTTP(_Response(429)), "key").search("A", "B")


async def test_search_maps_network_and_malformed_payload_to_unavailable():
    request = httpx.Request("GET", "https://example.test")
    network_error = httpx.ConnectError("offline", request=request)

    with pytest.raises(YouTubeAPIUnavailableError):
        await YouTubeSearchClient(_HTTP(error=network_error), "key").search("A", "B")

    with pytest.raises(YouTubeAPIUnavailableError):
        await YouTubeSearchClient(
            _HTTP(_Response(payload={"unexpected": []})), "key"
        ).search("A", "B")
