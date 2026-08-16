import httpx
import pytest

from preview import (
    MediaBinding,
    PreviewProviderUnavailable,
    _best_candidate,
    _best_itunes_candidate,
    _content_disposition,
    _fetch_deezer_preview,
    _fetch_itunes_preview,
    _lookup_media,
    _resolve_media,
    _retry_after_seconds,
    _search_queries,
    _strip_version,
    _version_markers,
    get_preview_url,
    stream_preview,
)
from recommend_algo.common import sources


def _item(track_id, title, artist, preview="https://cdn/p.mp3"):
    return {
        "id": track_id,
        "title": title,
        "artist": {"name": artist},
        "preview": preview,
    }


def _itunes(track_id, title, artist, preview="https://itunes/p.m4a"):
    # 실제 lookup·search 응답에는 kind가 들어 있다. ID 조회가 그 값을 본다.
    return {
        "kind": "song",
        "wrapperType": "track",
        "trackId": track_id,
        "trackName": title,
        "artistName": artist,
        "previewUrl": preview,
        "artworkUrl100": "https://itunes/100x100bb.jpg",
    }


@pytest.fixture(autouse=True)
def _isolate_provider_state():
    """공급자 캐시와 회로차단기는 모듈 전역이라 테스트 사이에 새어 나간다."""
    _resolve_media.cache_clear()
    _lookup_media.cache_clear()
    sources._ITUNES_RATE_LIMIT_UNTIL = 0.0
    sources._DZ_RATE_LIMIT_UNTIL = 0.0
    yield
    _resolve_media.cache_clear()
    _lookup_media.cache_clear()
    sources._ITUNES_RATE_LIMIT_UNTIL = 0.0
    sources._DZ_RATE_LIMIT_UNTIL = 0.0


# ── Content-Disposition ──────────────────────────────────────────


def test_content_disposition_encodes_korean_filename():
    header = _content_disposition("밤편지", "mp3")

    assert 'filename="preview.mp3"' in header
    assert "filename*=UTF-8''%EB%B0%A4%ED%8E%B8%EC%A7%80.mp3" in header
    assert header.encode("latin-1")


def test_content_disposition_uses_provider_extension():
    """iTunes 미리 듣기는 m4a다. .mp3로 내려보내면 형식이 어긋난다."""
    header = _content_disposition("밤편지", "m4a")

    assert 'filename="preview.m4a"' in header
    assert header.endswith(".m4a")


def test_content_disposition_escapes_header_control_characters():
    header = _content_disposition('title"\r\nX-Test: injected', "mp3")

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


@pytest.mark.parametrize("title,artist,want_title,want_artist", WRONG_ARTIST_CASES)
def test_itunes_candidates_use_the_same_policy(title, artist, want_title, want_artist):
    """채택 기준은 공급자와 무관하다. iTunes를 1순위로 올려도 같아야 한다."""
    results = [_itunes(1, title, artist)]

    assert _best_itunes_candidate(results, want_title, want_artist, frozenset()) is None


def test_best_candidate_prefers_correct_artist_over_first_result():
    items = [
        _item(1, "Through the Night [Piano]", "Flow Music"),
        _item(2, "Through the Night", "IU"),
    ]

    best = _best_candidate(items, "Through the Night", "IU", frozenset())

    assert best is not None
    assert (best.resolved_title, best.resolved_artist) == ("Through the Night", "IU")
    assert (best.provider, best.provider_track_id) == ("deezer", "2")


def test_best_candidate_keeps_requested_version():
    """`Creep (Acoustic)` 요청에 스튜디오판을 돌려주면 안 된다."""
    items = [_item(1, "Creep", "Radiohead"), _item(2, "Creep (Acoustic)", "Radiohead")]

    best = _best_candidate(items, "Creep", "Radiohead", frozenset({"acoustic"}))

    assert best is not None
    assert best.resolved_title == "Creep (Acoustic)"


def test_itunes_candidate_keeps_requested_version():
    results = [
        _itunes(1, "Creep", "Radiohead"),
        _itunes(2, "Creep (Acoustic)", "Radiohead"),
    ]

    best = _best_itunes_candidate(
        results, "Creep", "Radiohead", frozenset({"acoustic"})
    )

    assert best is not None
    assert best.resolved_title == "Creep (Acoustic)"


def test_best_candidate_prefers_plain_version_when_none_requested():
    items = [_item(1, "Creep (Live)", "Radiohead"), _item(2, "Creep", "Radiohead")]

    best = _best_candidate(items, "Creep", "Radiohead", frozenset())

    assert best is not None
    assert best.resolved_title == "Creep"


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


def test_best_candidate_skips_candidate_without_provider_id():
    """ID 없는 후보를 채택하면 재생 단계에서 또 문자열로 찾게 된다."""
    items = [_item(None, "Through the Night", "IU"), _item("", "Through the Night", "IU")]

    assert _best_candidate(items, "Through the Night", "IU", frozenset()) is None


def test_best_candidate_allows_collaboration_credit():
    items = [_item(1, "eight", "IU & SUGA")]

    best = _best_candidate(items, "eight", "IU", frozenset())

    assert best is not None
    assert best.resolved_artist == "IU & SUGA"


def test_best_candidate_ignores_artist_when_not_given():
    items = [_item(1, "Hello", "Adele")]

    best = _best_candidate(items, "Hello", "", frozenset())

    assert best is not None


def test_candidates_carry_provider_media_type():
    """MIME과 확장자는 공급자에서 나온다. 하드코딩하면 iTunes가 깨진다."""
    deezer = _best_candidate([_item(1, "Creep", "Radiohead")], "Creep", "", frozenset())
    itunes = _best_itunes_candidate(
        [_itunes(1, "Creep", "Radiohead")], "Creep", "", frozenset()
    )

    assert (deezer.content_type, deezer.file_extension) == ("audio/mpeg", "mp3")
    assert (itunes.content_type, itunes.file_extension) == ("audio/x-m4p", "m4a")
    assert itunes.artwork_url == "https://itunes/600x600bb.jpg"


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


# ── fake 공급자 ───────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload


class RoutingHttp:
    """iTunes와 Deezer를 구분해 응답하는 fake.

    `calls`는 두 공급자를 시간 순서대로 한 리스트에 담는다. 순서를 주장하려면
    공급자별 리스트만으로는 부족하다.
    """

    def __init__(self, itunes=(), deezer=(), itunes_response=None):
        self.itunes = list(itunes)
        self.deezer = list(deezer)
        self.itunes_response = itunes_response
        self.calls: list[tuple[str, str]] = []

    @property
    def itunes_terms(self) -> list[str]:
        return [query for provider, query in self.calls if provider == "itunes"]

    @property
    def deezer_queries(self) -> list[str]:
        return [query for provider, query in self.calls if provider == "deezer"]

    @property
    def providers(self) -> list[str]:
        return [provider for provider, _ in self.calls]

    async def get(self, url, params=None, timeout=None):
        params = params or {}
        if "itunes.apple.com" in url:
            self.calls.append(("itunes", params.get("term")))
            return self.itunes_response or FakeResponse({"results": self.itunes})
        self.calls.append(("deezer", params.get("q")))
        return FakeResponse({"data": self.deezer})


class LookupHttp:
    """ID 조회를 서빙하는 fake.

    두 공급자 모두 없는 ID에 HTTP 200을 돌려주므로 본문 모양까지 재현한다.
    """

    def __init__(self, itunes=None, deezer=None, response=None):
        self.itunes = itunes
        self.deezer = deezer
        self.response = response
        self.calls: list[tuple[str, str]] = []

    @property
    def searches(self) -> list[tuple[str, str]]:
        return [row for row in self.calls if row[0].endswith("search")]

    async def get(self, url, params=None, timeout=None):
        params = params or {}
        if "itunes.apple.com/lookup" in url:
            self.calls.append(("itunes-lookup", str(params.get("id"))))
            if self.response is not None:
                return self.response
            results = [self.itunes] if self.itunes else []
            return FakeResponse({"resultCount": len(results), "results": results})
        if "api.deezer.com/track/" in url:
            self.calls.append(("deezer-lookup", url.rsplit("/", 1)[-1]))
            if self.response is not None:
                return self.response
            if self.deezer is None:
                return FakeResponse(
                    {"error": {"type": "DataException", "message": "no data", "code": 800}}
                )
            return FakeResponse(self.deezer)
        # 검색으로 새면 안 된다. 어느 쪽이든 기록해 두고 빈 결과를 준다.
        if "itunes.apple.com" in url:
            self.calls.append(("itunes-search", str(params.get("term"))))
            return FakeResponse({"results": []})
        self.calls.append(("deezer-search", str(params.get("q"))))
        return FakeResponse({"data": []})


class FakeHttp:
    """Deezer만 아는 fake. _fetch_deezer_preview 전용."""

    def __init__(self, items):
        self.items = items
        self.queries = []

    async def get(self, url, params=None, timeout=None):
        self.queries.append((params or {}).get("q"))
        return FakeResponse({"data": self.items})


class StaticResponseHttp:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self.response


class InvalidJsonResponse:
    status_code = 200
    headers: dict = {}

    def json(self):
        raise ValueError("invalid json")


def _request(http):
    class _State:
        pass

    state = _State()
    state.http = http
    app = _State()
    app.state = state
    request = _State()
    request.app = app
    return request


# ── Deezer 경로 (_fetch_deezer_preview) ──────────────────────────────────


async def test_fetch_deezer_preview_returns_none_when_only_wrong_artists():
    http = FakeHttp([_item(1, "Through the Night [Piano]", "Flow Music")])

    assert await _fetch_deezer_preview(http, "Through the Night", "IU") is None


async def test_fetch_deezer_preview_searches_requested_version_first():
    http = FakeHttp([_item(1, "Creep (Acoustic)", "Radiohead")])

    match = await _fetch_deezer_preview(http, "Creep (Acoustic)", "Radiohead")

    assert match is not None
    assert match.resolved_title == "Creep (Acoustic)"
    assert http.queries == ['track:"Creep (Acoustic)" artist:"Radiohead"']


async def test_fetch_deezer_preview_stops_after_deezer_server_error():
    http = StaticResponseHttp(FakeResponse({"data": []}, status_code=500))

    with pytest.raises(PreviewProviderUnavailable) as exc:
        await _fetch_deezer_preview(http, "Creep", "Radiohead")

    assert exc.value.retry_after == "30"
    assert http.calls == 1


async def test_fetch_deezer_preview_stops_after_invalid_json():
    http = StaticResponseHttp(InvalidJsonResponse())

    with pytest.raises(PreviewProviderUnavailable):
        await _fetch_deezer_preview(http, "Creep", "Radiohead")

    assert http.calls == 1


async def test_fetch_deezer_preview_stops_after_network_error():
    class NetworkErrorHttp:
        def __init__(self):
            self.calls = 0

        async def get(self, url, params=None, timeout=None):
            self.calls += 1
            raise httpx.ConnectError("connection failed")

    http = NetworkErrorHttp()

    with pytest.raises(PreviewProviderUnavailable) as exc:
        await _fetch_deezer_preview(http, "Creep (Acoustic)", "Radiohead")

    assert exc.value.retry_after == "30"
    assert http.calls == 1


# ── 공급자 선택 순서 (_resolve_media) ─────────────────────────────


async def test_itunes_wins_when_both_providers_have_the_track():
    http = RoutingHttp(
        itunes=[_itunes(11, "Creep", "Radiohead")],
        deezer=[_item(22, "Creep", "Radiohead")],
    )

    match = await _resolve_media(http, "Creep", "Radiohead")

    assert (match.provider, match.provider_track_id) == ("itunes", "11")
    # iTunes가 확정했으면 Deezer는 부르지 않는다.
    assert http.deezer_queries == []


async def test_falls_back_to_deezer_when_itunes_has_nothing():
    """`Through the Night / IU`의 반대 사례. Deezer만 가진 곡도 재생돼야 한다."""
    http = RoutingHttp(itunes=[], deezer=[_item(22, "기다리다", "Younha")])

    match = await _resolve_media(http, "기다리다", "Younha")

    assert (match.provider, match.provider_track_id) == ("deezer", "22")
    # iTunes를 모두 소진한 뒤에야 Deezer로 넘어간다.
    assert http.providers == ["itunes", "deezer"]
    assert http.itunes_terms == ["기다리다 Younha"]
    assert http.deezer_queries == ['track:"기다리다" artist:"Younha"']


async def test_itunes_exhausts_its_query_forms_before_deezer():
    """버전 표기가 있으면 iTunes도 요청 표기와 기본 제목을 차례로 시도한다."""
    http = RoutingHttp(itunes=[], deezer=[_item(22, "Creep (Acoustic)", "Radiohead")])

    await _resolve_media(http, "Creep (Acoustic)", "Radiohead")

    assert http.itunes_terms == ["Creep (Acoustic) Radiohead", "Creep Radiohead"]
    assert http.providers[: len(http.itunes_terms)] == ["itunes", "itunes"]
    assert http.providers[len(http.itunes_terms)] == "deezer"


async def test_falls_back_to_deezer_when_itunes_has_only_wrong_artist():
    """iTunes 응답이 비지 않아도 검증에 걸리면 Deezer로 넘어가야 한다."""
    http = RoutingHttp(
        itunes=[_itunes(11, "Through The Night", "Maeta")],
        deezer=[_item(22, "Through the Night", "IU")],
    )

    match = await _resolve_media(http, "Through the Night", "IU")

    assert (match.provider, match.provider_track_id) == ("deezer", "22")


async def test_itunes_rate_limit_falls_back_instead_of_failing():
    """iTunes 429는 503이 아니다. Deezer가 같은 곡을 가지고 있을 수 있다."""
    http = RoutingHttp(
        itunes_response=FakeResponse({}, status_code=429, headers={"Retry-After": "5"}),
        deezer=[_item(22, "Creep", "Radiohead")],
    )

    match = await _resolve_media(http, "Creep", "Radiohead")

    assert match.provider == "deezer"
    assert sources._is_itunes_rate_limited()


async def test_itunes_circuit_breaker_skips_the_search_entirely():
    sources._mark_itunes_rate_limited(30)
    http = RoutingHttp(
        itunes=[_itunes(11, "Creep", "Radiohead")],
        deezer=[_item(22, "Creep", "Radiohead")],
    )

    match = await _resolve_media(http, "Creep", "Radiohead")

    assert match.provider == "deezer"
    assert http.itunes_terms == []


class BreakerOpeningHttp:
    """첫 응답을 준 직후 다른 요청이 차단기를 연 상황을 만든다."""

    def __init__(self, open_breaker):
        self.calls = []
        self._open_breaker = open_breaker

    async def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        self._open_breaker()
        # 매치가 없는 정상 응답. 루프는 다음 검색어로 넘어가려 한다.
        return FakeResponse({"resultCount": 0, "results": [], "data": []})


async def test_itunes_search_stops_when_the_breaker_opens_between_terms():
    """검색어 사이에 열린 차단기를 못 보면 남은 검색어가 그대로 나간다."""
    http = BreakerOpeningHttp(lambda: sources._mark_itunes_rate_limited(30))

    assert await _fetch_itunes_preview(http, "Creep (Acoustic)", "Radiohead") is None
    assert len(http.calls) == 1


async def test_deezer_search_stops_when_the_breaker_opens_between_queries():
    http = BreakerOpeningHttp(
        lambda: sources._mark_dz_rate_limited({"Retry-After": "30"})
    )

    with pytest.raises(PreviewProviderUnavailable):
        await _fetch_deezer_preview(http, "Creep (Acoustic)", "Radiohead")
    assert len(http.calls) == 1


async def test_no_match_is_returned_as_none_not_raised():
    """확정적 미매치는 예외가 아니다.

    라우트는 None을 404로 옮기고 `PreviewProviderUnavailable`만 잡는다. 여기서
    예외를 던지면 없는 곡이 404가 아니라 500이 된다.
    """
    http = RoutingHttp(itunes=[], deezer=[])

    assert await _resolve_media(http, "Nonexistent", "Nobody") is None


async def test_no_match_is_cached_so_a_retry_costs_no_provider_call():
    """미매치도 캐시된다. 예외로 바꾸면 alru_cache가 저장하지 않아 사라진다."""
    http = RoutingHttp(itunes=[], deezer=[])

    await _resolve_media(http, "Nonexistent", "Nobody")
    after_first = len(http.calls)
    await _resolve_media(http, "Nonexistent", "Nobody")

    assert after_first > 0
    assert len(http.calls) == after_first


async def test_resolve_media_caches_the_confirmed_binding():
    """같은 곡을 다시 눌러도 공급자를 다시 조회하지 않는다."""
    http = RoutingHttp(itunes=[_itunes(11, "Creep", "Radiohead")])

    first = await _resolve_media(http, "Creep", "Radiohead")
    second = await _resolve_media(http, "Creep", "Radiohead")

    assert first == second
    assert len(http.itunes_terms) == 1


# ── ID 조회 (5-a) ────────────────────────────────────────────────

DEEZER_TRACK = {
    "id": 2215315187,
    "title": "Creep (Acoustic)",
    "artist": {"name": "Radiohead"},
    "album": {"cover_big": "https://dz/cover.jpg"},
    "preview": "https://cdn/p.mp3",
    "isrc": "GBAYE9300465",
}


async def test_itunes_lookup_makes_no_search():
    """ID 조회는 검색이 아니다. 후보 판정 자체가 필요 없다."""
    http = LookupHttp(itunes=_itunes(1229073406, "Through the Night", "IU"))

    binding = await _lookup_media(http, "itunes", "1229073406")

    assert (binding.provider, binding.provider_track_id) == ("itunes", "1229073406")
    assert binding.content_type == "audio/x-m4p"
    assert http.calls == [("itunes-lookup", "1229073406")]
    assert http.searches == []


async def test_deezer_lookup_makes_no_search_and_captures_isrc():
    """Deezer는 응답에 ISRC를 실어 준다. 공급자 간 동일곡 판정의 유일한 축이다."""
    http = LookupHttp(deezer=DEEZER_TRACK)

    binding = await _lookup_media(http, "deezer", "2215315187")

    assert (binding.provider, binding.provider_track_id) == ("deezer", "2215315187")
    assert binding.isrc == "GBAYE9300465"
    assert http.calls == [("deezer-lookup", "2215315187")]
    assert http.searches == []


async def test_itunes_binding_has_no_isrc():
    """iTunes는 ISRC를 아예 주지 않는다. 없는 것을 지어내지 않는다."""
    http = LookupHttp(itunes=_itunes(1, "Creep", "Radiohead"))

    binding = await _lookup_media(http, "itunes", "1")

    assert binding.isrc is None


async def test_lookup_ignores_catalog_spelling():
    """문자열 경로에서 404였던 사례. ID로는 표기 차이가 문제되지 않는다.

    Deezer는 `밤편지`를 `Through the Night`으로 싣는다. 검색이면 제목이 달라
    탈락하지만, 조회는 판정할 것이 없다.
    """
    http = LookupHttp(itunes=_itunes(1229073406, "Through the Night", "IU"))

    binding = await _lookup_media(http, "itunes", "1229073406")

    assert binding.resolved_title == "Through the Night"


MISSING_ID_CASES = [
    pytest.param("itunes", "999999999999", {}, id="itunes-없는-id"),
    pytest.param("deezer", "999999999999", {}, id="deezer-없는-id"),
]


@pytest.mark.parametrize("provider,track_id,_unused", MISSING_ID_CASES)
async def test_missing_id_is_none_not_an_error(provider, track_id, _unused):
    """두 공급자 모두 없는 ID에 HTTP 200을 준다. 본문으로 판별해야 한다."""
    http = LookupHttp()

    assert await _lookup_media(http, provider, track_id) is None


async def test_itunes_lookup_rejects_a_result_with_a_different_id():
    """앨범 ID를 넣으면 collection 한 줄과 수록곡 전부가 함께 온다.

    실측: 앨범 1097861387을 entity=song으로 조회하면 13건이 오고 첫 재생 가능한
    항목이 Airbag(1097861769)이다. 그걸 집으면 요청하지 않은 곡이 나가고, 이
    경로의 유일한 보장인 "정확히 그 곡"이 깨진다.
    """
    album_rows = [
        {"wrapperType": "collection", "collectionId": 1097861387},
        _itunes(1097861769, "Airbag", "Radiohead"),
        _itunes(1097861770, "Paranoid Android", "Radiohead"),
    ]
    http = LookupHttp(response=FakeResponse({"resultCount": 3, "results": album_rows}))

    assert await _lookup_media(http, "itunes", "1097861387") is None


async def test_itunes_lookup_accepts_the_matching_song_row():
    rows = [
        {"wrapperType": "collection", "collectionId": 1097861387},
        _itunes(1097861769, "Airbag", "Radiohead"),
    ]
    http = LookupHttp(response=FakeResponse({"resultCount": 2, "results": rows}))

    binding = await _lookup_media(http, "itunes", "1097861769")

    assert binding.resolved_title == "Airbag"


async def test_itunes_lookup_rejects_a_non_song_kind():
    """같은 ID라도 곡이 아니면 재생 대상이 아니다."""
    rows = [_itunes(1, "Some Video", "Radiohead") | {"kind": "music-video"}]
    http = LookupHttp(response=FakeResponse({"resultCount": 1, "results": rows}))

    assert await _lookup_media(http, "itunes", "1") is None


async def test_unknown_provider_is_none():
    http = LookupHttp()

    assert await _lookup_media(http, "spotify", "4") is None
    assert http.calls == []


async def test_deezer_quota_error_is_not_cached_as_missing():
    """쿼터 초과도 200 + error로 온다. 미수록으로 캐시하면 회복이 안 된다."""
    http = LookupHttp(
        response=FakeResponse(
            {"error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}}
        )
    )

    with pytest.raises(PreviewProviderUnavailable):
        await _lookup_media(http, "deezer", "1")


async def test_itunes_lookup_rate_limit_opens_the_breaker():
    """조회 경로에는 fallback이 없다. 검색 경로와 달리 503으로 올린다."""
    http = LookupHttp(
        response=FakeResponse({}, status_code=429, headers={"Retry-After": "12"})
    )

    with pytest.raises(PreviewProviderUnavailable) as exc:
        await _lookup_media(http, "itunes", "1")

    assert exc.value.retry_after == "12"
    assert sources._is_itunes_rate_limited()


async def test_open_breaker_skips_the_itunes_lookup():
    sources._mark_itunes_rate_limited(30)
    http = LookupHttp(itunes=_itunes(1, "Creep", "Radiohead"))

    with pytest.raises(PreviewProviderUnavailable):
        await _lookup_media(http, "itunes", "1")

    assert http.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            FakeResponse({}, status_code=429, headers={"Retry-After": "9"}),
            id="http-429",
        ),
        pytest.param(
            FakeResponse(
                {"error": {"type": "Exception", "message": "Quota", "code": 4}}
            ),
            id="quota-200",
        ),
    ],
)
async def test_deezer_lookup_records_the_shared_breaker(payload):
    """기록하지 않으면 제한 중에도 클릭마다 Deezer를 다시 부른다."""
    sources._DZ_RATE_LIMIT_UNTIL = 0.0
    http = LookupHttp(response=payload)

    with pytest.raises(PreviewProviderUnavailable):
        await _lookup_media(http, "deezer", "1")

    assert sources._is_dz_rate_limited()


async def test_open_deezer_breaker_skips_the_lookup():
    sources._mark_dz_rate_limited({"Retry-After": "30"})
    http = LookupHttp(deezer=DEEZER_TRACK)

    with pytest.raises(PreviewProviderUnavailable):
        await _lookup_media(http, "deezer", "2215315187")

    assert http.calls == []


async def test_deezer_search_treats_a_quota_error_as_unavailable():
    """검색 응답의 쿼터 초과는 `data`가 없어 빈 결과처럼 보인다.

    그대로 두면 남은 검색어를 계속 시도하고 끝내 404가 된다. 제한을 미수록으로
    오해하는 것이고, 차단기도 닫힌 채라 다음 클릭이 또 두드린다. 재현 시
    provider_calls=3, breaker_open=False였다.
    """

    class QuotaHttp:
        def __init__(self):
            self.calls = 0

        async def get(self, url, params=None, timeout=None):
            self.calls += 1
            return FakeResponse(
                {"error": {"type": "Exception", "message": "Quota", "code": 4}}
            )

    http = QuotaHttp()

    with pytest.raises(PreviewProviderUnavailable) as exc:
        await _fetch_deezer_preview(http, "Creep", "Radiohead")

    assert exc.value.retry_after == "60"
    # 첫 응답에서 끊는다. 남은 검색어를 더 시도하지 않는다.
    assert http.calls == 1
    assert sources._is_dz_rate_limited()


async def test_deezer_search_still_treats_other_errors_as_no_match():
    """쿼터가 아닌 error는 미수록이다. 503으로 올리면 안 된다."""

    class DataErrorHttp:
        def __init__(self):
            self.calls = 0

        async def get(self, url, params=None, timeout=None):
            self.calls += 1
            return FakeResponse(
                {"error": {"type": "DataException", "message": "no data", "code": 800}}
            )

    http = DataErrorHttp()

    assert await _fetch_deezer_preview(http, "Creep", "Radiohead") is None
    assert not sources._is_dz_rate_limited()


async def test_open_deezer_breaker_skips_the_search_too():
    """조회 경로만 막으면 검색 경로가 그대로 제한을 두드린다."""
    sources._mark_dz_rate_limited({"Retry-After": "30"})
    http = FakeHttp([_item(1, "Creep", "Radiohead")])

    with pytest.raises(PreviewProviderUnavailable):
        await _fetch_deezer_preview(http, "Creep", "Radiohead")

    assert http.queries == []


async def test_lookup_result_is_cached():
    http = LookupHttp(itunes=_itunes(1, "Creep", "Radiohead"))

    await _lookup_media(http, "itunes", "1")
    await _lookup_media(http, "itunes", "1")

    assert len(http.calls) == 1


# ── 라우트 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("route", [get_preview_url, stream_preview])
async def test_preview_routes_report_deezer_rate_limit_as_503(route):
    from fastapi import HTTPException

    http = StaticResponseHttp(
        FakeResponse({"data": []}, status_code=429, headers={"Retry-After": "17"})
    )

    with pytest.raises(HTTPException) as exc:
        await route(_request(http), track="Creep", artist="Radiohead")

    assert exc.value.status_code == 503
    assert exc.value.headers == {"Retry-After": "17"}
    assert http.calls == 2  # iTunes 429 뒤 Deezer 429, 공급자별 한 번씩


# 공급자가 준 Retry-After를 그대로 응답 헤더에 넣으므로, delta-seconds가 아닌
# 값은 전부 기본값으로 갈아치워야 한다. 특히 CRLF가 중간에 박힌 값이 통과하면
# 헤더 인젝션이 된다.
HOSTILE_RETRY_AFTER = [
    pytest.param("bad", id="not-a-number"),
    pytest.param("bad\r\nX-Injected: evil", id="crlf-in-the-middle"),
    pytest.param("17\r\nX-Injected: evil", id="numeric-prefix-then-crlf"),
    pytest.param("17\nX-Injected: evil", id="bare-lf"),
    pytest.param("Wed, 21 Oct 2015 07:28:00 GMT", id="http-date"),
    pytest.param("-5", id="negative"),
    pytest.param("", id="empty"),
]


@pytest.mark.parametrize("value", HOSTILE_RETRY_AFTER)
async def test_preview_rate_limit_replaces_invalid_retry_after(value):
    from fastapi import HTTPException

    http = StaticResponseHttp(
        FakeResponse({"data": []}, status_code=429, headers={"Retry-After": value})
    )

    with pytest.raises(HTTPException) as exc:
        await get_preview_url(_request(http), track="Creep", artist="Radiohead")

    assert exc.value.status_code == 503
    assert exc.value.headers == {"Retry-After": "60"}


def test_retry_after_never_emits_header_control_characters():
    for value in ("17\r\nX: y", "17\n\n", "\r\n17"):
        assert "\r" not in _retry_after_seconds(value, default=60)
        assert "\n" not in _retry_after_seconds(value, default=60)


async def test_get_preview_url_returns_resolved_track_not_echo():
    """응답의 track·artist는 요청값 echo가 아니라 실제 선택된 곡이어야 한다."""
    http = RoutingHttp(deezer=[_item(1, "eight", "IU & SUGA")])

    payload = await get_preview_url(_request(http), track="eight", artist="IU")

    assert payload["artist"] == "IU & SUGA"
    assert payload["requested"] == {
        "track": "eight",
        "artist": "IU",
        "provider": "",
        "provider_track_id": "",
    }


async def test_get_preview_url_exposes_provider_binding():
    http = RoutingHttp(itunes=[_itunes(11, "Creep", "Radiohead")])

    payload = await get_preview_url(_request(http), track="Creep", artist="Radiohead")

    assert payload["provider"] == "itunes"
    assert payload["provider_track_id"] == "11"
    assert payload["content_type"] == "audio/x-m4p"
    assert payload["artwork_url"] == "https://itunes/600x600bb.jpg"


async def test_get_preview_url_404_when_catalog_title_differs():
    """카탈로그 표기가 다르면 추측하지 않고 404를 낸다.

    `밤편지`를 카탈로그는 `Through the Night`로 싣는다. 아티스트가 맞아도 아무
    IU 곡이나 돌려주면 또 다른 오답 재생이 되므로 포기한다. 프론트는 추천
    응답의 resolved 제목을 넘기므로 제품 경로에서는 발생하지 않는다.
    """
    from fastapi import HTTPException

    http = RoutingHttp(deezer=[_item(1, "Through the Night", "IU")])

    with pytest.raises(HTTPException) as exc:
        await get_preview_url(_request(http), track="밤편지", artist="IU")

    assert exc.value.status_code == 404


@pytest.mark.parametrize("route", [get_preview_url, stream_preview])
async def test_routes_take_the_id_path_when_given_one(route):
    """ID가 오면 곡명은 쳐다보지 않는다. 검색이 0회여야 한다."""
    http = LookupHttp(itunes=_itunes(1229073406, "Through the Night", "IU"))

    result = await route(
        _request(http),
        track="완전히 다른 제목",
        provider="itunes",
        provider_track_id="1229073406",
    )

    assert result is not None
    assert http.searches == []
    assert http.calls == [("itunes-lookup", "1229073406")]


async def test_id_path_response_carries_the_binding():
    http = LookupHttp(deezer=DEEZER_TRACK)

    payload = await get_preview_url(
        _request(http), provider="deezer", provider_track_id="2215315187"
    )

    assert payload["provider"] == "deezer"
    assert payload["provider_track_id"] == "2215315187"
    assert payload["isrc"] == "GBAYE9300465"
    assert payload["track"] == "Creep (Acoustic)"
    assert payload["requested"]["provider_track_id"] == "2215315187"


async def test_missing_id_returns_404_not_a_search_fallback():
    """ID를 줬는데 없으면 404다. 조용히 문자열 검색으로 내려가면 클라이언트가
    다른 곡을 받게 된다."""
    from fastapi import HTTPException

    http = LookupHttp()

    with pytest.raises(HTTPException) as exc:
        await get_preview_url(
            _request(http), track="Creep", provider="itunes", provider_track_id="99"
        )

    assert exc.value.status_code == 404
    assert http.searches == []


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({}, id="아무것도-없음"),
        pytest.param({"provider": "itunes"}, id="id-없는-provider"),
        pytest.param({"provider_track_id": "1"}, id="provider-없는-id"),
        # 곡명이 함께 와도 반쪽 ID는 거절한다. 조용히 이름 검색으로 흘려보내면
        # 클라이언트가 지목한 곡 대신 다른 곡이 재생될 수 있다.
        pytest.param(
            {"track": "Creep", "provider": "itunes"}, id="곡명+provider-반쪽"
        ),
        pytest.param(
            {"track": "Creep", "provider_track_id": "1"}, id="곡명+id-반쪽"
        ),
    ],
)
async def test_routes_reject_a_request_with_no_target(kwargs):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await get_preview_url(_request(LookupHttp()), **kwargs)

    assert exc.value.status_code == 422


async def test_string_path_still_works_without_an_id():
    """기존 경로는 fallback으로 남는다."""
    http = RoutingHttp(itunes=[_itunes(11, "Creep", "Radiohead")])

    payload = await get_preview_url(_request(http), track="Creep", artist="Radiohead")

    assert payload["provider_track_id"] == "11"
    assert http.itunes_terms == ["Creep Radiohead"]


async def test_get_preview_url_404_when_nothing_matches():
    from fastapi import HTTPException

    http = RoutingHttp(deezer=[_item(1, "Hello", "Adele")])

    with pytest.raises(HTTPException) as exc:
        await get_preview_url(
            _request(http), track="Hello", artist="Definitely Not Adele"
        )

    assert exc.value.status_code == 404


@pytest.mark.parametrize(
    "provider,binding_kwargs,want_extension,want_media_type",
    [
        ("deezer", {"content_type": "audio/mpeg", "file_extension": "mp3"}, "mp3", "audio/mpeg"),
        ("itunes", {"content_type": "audio/x-m4p", "file_extension": "m4a"}, "m4a", "audio/x-m4p"),
    ],
)
async def test_stream_preview_uses_provider_media_type(
    monkeypatch, provider, binding_kwargs, want_extension, want_media_type
):
    async def fake_resolve(http, track_name, artist):
        return MediaBinding(
            provider=provider,
            provider_track_id="123",
            preview_url="https://example.com/preview",
            resolved_title="밤편지",
            resolved_artist="IU",
            **binding_kwargs,
        )

    monkeypatch.setattr("preview._resolve_media", fake_resolve)

    response = await stream_preview(_request(object()), track="밤편지", artist="IU")

    assert response.status_code == 200
    assert response.media_type == want_media_type
    assert response.headers["content-disposition"] == (
        f'inline; filename="preview.{want_extension}"; '
        f"filename*=UTF-8''%EB%B0%A4%ED%8E%B8%EC%A7%80.{want_extension}"
    )
    # Range를 지원하지 않으므로 Accept-Ranges를 광고하지 않는다.
    assert "accept-ranges" not in response.headers
    await response.body_iterator.aclose()
