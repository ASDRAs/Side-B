from types import SimpleNamespace

import httpx
import pytest

from preview import (
    PreviewMatch,
    PreviewProviderUnavailable,
    _best_candidate,
    _content_disposition,
    _fetch_preview,
    _search_queries,
    _strip_version,
    _version_markers,
    get_preview_url,
    stream_preview,
)


def _item(track_id, title, artist, preview="https://cdn/p.mp3"):
    return {
        "id": track_id,
        "title": title,
        "artist": {"name": artist},
        "preview": preview,
    }


# ── Content-Disposition ──────────────────────────────────────────


def test_content_disposition_encodes_korean_filename():
    header = _content_disposition("밤편지")

    assert 'filename="preview.mp3"' in header
    assert "filename*=UTF-8''%EB%B0%A4%ED%8E%B8%EC%A7%80.mp3" in header
    assert header.encode("latin-1")


def test_content_disposition_escapes_header_control_characters():
    header = _content_disposition('title"\r\nX-Test: injected')

    assert "\r" not in header
    assert "\n" not in header
    assert 'title"' not in header
    assert "%22%0D%0A" in header


# ── 후보 선택 ─────────────────────────────────────────────────────
# 배포본에서 실제로 잘못 선택된 사례들. 전부 오답 아티스트가 1순위였다.

WRONG_ARTIST_CASES = [
    pytest.param(
        "Through the Night [Piano]", "Flow Music", "Through the Night", "IU",
        id="밤편지-flow-music",
    ),
    pytest.param(
        "Through The Night", "Maeta", "Through the Night", "IU", id="seed-maeta"
    ),
    pytest.param("Hello", "Adele", "Hello", "Definitely Not Adele", id="not-adele"),
    pytest.param("If I could tell you", "TAEMIN", "If", "TAEYEON", id="taemin"),
]


@pytest.mark.parametrize("title,artist,want_title,want_artist", WRONG_ARTIST_CASES)
def test_best_candidate_rejects_wrong_artist(title, artist, want_title, want_artist):
    items = [_item(1, title, artist)]

    assert _best_candidate(items, want_title, want_artist, frozenset()) is None


def test_best_candidate_prefers_correct_artist_over_first_result():
    items = [
        _item(1, "Through the Night [Piano]", "Flow Music"),
        _item(2, "Through the Night", "IU"),
    ]

    best = _best_candidate(items, "Through the Night", "IU", frozenset())

    assert best is not None
    assert (best.track, best.artist, best.deezer_id) == ("Through the Night", "IU", "2")


def test_best_candidate_keeps_requested_version():
    """`Creep (Acoustic)` 요청에 스튜디오판을 돌려주면 안 된다."""
    items = [_item(1, "Creep", "Radiohead"), _item(2, "Creep (Acoustic)", "Radiohead")]

    best = _best_candidate(items, "Creep", "Radiohead", frozenset({"acoustic"}))

    assert best is not None
    assert best.track == "Creep (Acoustic)"


def test_best_candidate_prefers_plain_version_when_none_requested():
    items = [_item(1, "Creep (Live)", "Radiohead"), _item(2, "Creep", "Radiohead")]

    best = _best_candidate(items, "Creep", "Radiohead", frozenset())

    assert best is not None
    assert best.track == "Creep"


def test_best_candidate_rejects_unrequested_remix():
    items = [_item(1, "Shape Of You (Dubstep Remix)", "Ed Sheeran")]

    assert _best_candidate(items, "Shape of You", "Ed Sheeran", frozenset()) is None


def test_best_candidate_rejects_short_partial_title_from_same_artist():
    items = [_item(1, "I", "TAEYEON")]

    assert _best_candidate(items, "If", "TAEYEON", frozenset()) is None


def test_best_candidate_skips_karaoke_and_missing_preview():
    items = [
        _item(1, "Through the Night (Karaoke Version)", "IU"),
        _item(2, "Through the Night", "IU", preview=""),
    ]

    assert _best_candidate(items, "Through the Night", "IU", frozenset()) is None


def test_best_candidate_allows_collaboration_credit():
    items = [_item(1, "eight", "IU & SUGA")]

    best = _best_candidate(items, "eight", "IU", frozenset())

    assert best is not None
    assert best.artist == "IU & SUGA"


def test_best_candidate_ignores_artist_when_not_given():
    items = [_item(1, "Hello", "Adele")]

    best = _best_candidate(items, "Hello", "", frozenset())

    assert best is not None


def test_version_parser_preserves_non_version_parentheses_and_word_boundaries():
    assert _strip_version("너랑 나 (YOU&I)") == "너랑 나 (YOU&I)"
    assert _strip_version("Creep (Acoustic)") == "Creep"
    assert _strip_version("Creep - Remastered 2011") == "Creep"
    assert _version_markers("Alive") == frozenset()
    assert _version_markers("Live Forever") == frozenset()
    assert _version_markers("Piano Man") == frozenset()


def test_search_queries_preserve_requested_version_before_base_title():
    queries = _search_queries("Creep (Acoustic)", "Radiohead")

    assert queries[:2] == [
        'track:"Creep (Acoustic)" artist:"Radiohead"',
        "Creep (Acoustic) Radiohead",
    ]
    assert queries.index('track:"Creep" artist:"Radiohead"') > 1


# ── 라우트 ───────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, items, status_code=200, headers=None):
        self._items = items
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return {"data": self._items}


class FakeHttp:
    def __init__(self, items):
        self.items = items
        self.queries = []

    async def get(self, url, params=None, timeout=None):
        self.queries.append((params or {}).get("q"))
        return FakeResponse(self.items)


class StaticResponseHttp:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self.response


class InvalidJsonResponse:
    status_code = 200
    headers = {}

    def json(self):
        raise ValueError("invalid json")


def _request(http):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(http=http)))


async def test_fetch_preview_returns_none_when_only_wrong_artists():
    http = FakeHttp([_item(1, "Through the Night [Piano]", "Flow Music")])

    assert await _fetch_preview(http, "Through the Night", "IU") is None


async def test_fetch_preview_searches_requested_version_first():
    http = FakeHttp([_item(1, "Creep (Acoustic)", "Radiohead")])

    match = await _fetch_preview(http, "Creep (Acoustic)", "Radiohead")

    assert match is not None
    assert match.track == "Creep (Acoustic)"
    assert http.queries == ['track:"Creep (Acoustic)" artist:"Radiohead"']


async def test_fetch_preview_stops_after_deezer_server_error():
    http = StaticResponseHttp(FakeResponse([], status_code=500))

    with pytest.raises(PreviewProviderUnavailable) as exc:
        await _fetch_preview(http, "Creep", "Radiohead")

    assert exc.value.retry_after == "30"
    assert http.calls == 1


async def test_fetch_preview_stops_after_invalid_json():
    http = StaticResponseHttp(InvalidJsonResponse())

    with pytest.raises(PreviewProviderUnavailable):
        await _fetch_preview(http, "Creep", "Radiohead")

    assert http.calls == 1


async def test_fetch_preview_stops_after_network_error():
    class NetworkErrorHttp:
        def __init__(self):
            self.calls = 0

        async def get(self, url, params=None, timeout=None):
            self.calls += 1
            raise httpx.ConnectError("connection failed")

    http = NetworkErrorHttp()

    with pytest.raises(PreviewProviderUnavailable) as exc:
        await _fetch_preview(http, "Creep (Acoustic)", "Radiohead")

    assert exc.value.retry_after == "30"
    assert http.calls == 1


@pytest.mark.parametrize("route", [get_preview_url, stream_preview])
async def test_preview_routes_report_deezer_rate_limit_as_503(route):
    from fastapi import HTTPException

    http = StaticResponseHttp(
        FakeResponse([], status_code=429, headers={"Retry-After": "17"})
    )

    with pytest.raises(HTTPException) as exc:
        await route(_request(http), track="Creep", artist="Radiohead")

    assert exc.value.status_code == 503
    assert exc.value.headers == {"Retry-After": "17"}
    assert http.calls == 1


async def test_preview_rate_limit_replaces_invalid_retry_after():
    from fastapi import HTTPException

    http = StaticResponseHttp(
        FakeResponse([], status_code=429, headers={"Retry-After": "bad\r\nheader"})
    )

    with pytest.raises(HTTPException) as exc:
        await get_preview_url(_request(http), track="Creep", artist="Radiohead")

    assert exc.value.status_code == 503
    assert exc.value.headers == {"Retry-After": "60"}


async def test_get_preview_url_returns_resolved_track_not_echo():
    """응답의 track·artist는 요청값 echo가 아니라 실제 선택된 곡이어야 한다."""
    http = FakeHttp([_item(1, "eight", "IU & SUGA")])

    payload = await get_preview_url(_request(http), track="eight", artist="IU")

    assert payload["artist"] == "IU & SUGA"
    assert payload["requested"] == {"track": "eight", "artist": "IU"}


async def test_get_preview_url_404_when_catalog_title_differs():
    """카탈로그 표기가 다르면 추측하지 않고 404를 낸다.

    `밤편지`를 Deezer는 `Through the Night`로 싣는다. 아티스트가 맞아도 아무
    IU 곡이나 돌려주면 또 다른 오답 재생이 되므로 포기한다. 프론트는 추천
    응답의 resolved 제목을 넘기므로 제품 경로에서는 발생하지 않는다.
    """
    from fastapi import HTTPException

    http = FakeHttp([_item(1, "Through the Night", "IU")])

    with pytest.raises(HTTPException) as exc:
        await get_preview_url(_request(http), track="밤편지", artist="IU")

    assert exc.value.status_code == 404


async def test_get_preview_url_404_when_nothing_matches():
    from fastapi import HTTPException

    http = FakeHttp([_item(1, "Hello", "Adele")])

    with pytest.raises(HTTPException) as exc:
        await get_preview_url(_request(http), track="Hello", artist="Definitely Not Adele")

    assert exc.value.status_code == 404


async def test_stream_preview_names_file_after_resolved_track(monkeypatch):
    async def fake_fetch_preview(http, track_name, artist):
        return PreviewMatch(
            preview_url="https://example.com/preview.mp3",
            deezer_id="123",
            track="밤편지",
            artist="IU",
        )

    monkeypatch.setattr("preview._fetch_preview", fake_fetch_preview)

    response = await stream_preview(_request(object()), track="밤편지", artist="IU")

    assert response.status_code == 200
    assert (
        response.headers["content-disposition"]
        == 'inline; filename="preview.mp3"; '
        "filename*=UTF-8''%EB%B0%A4%ED%8E%B8%EC%A7%80.mp3"
    )
    # Range를 지원하지 않으므로 Accept-Ranges를 광고하지 않는다.
    assert "accept-ranges" not in response.headers
    await response.body_iterator.aclose()
