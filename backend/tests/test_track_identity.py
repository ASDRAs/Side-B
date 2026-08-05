import pytest

from recommend_algo.common import sources
from recommend_algo.common.models import (
    TrackInfo,
    binding_from_source_id,
    track_to_api_dict,
)
from recommend_algo.common.sources import (
    _deezer_binding,
    _itunes_binding,
    get_tracks_metadata,
)

# PR 1은 외부 응답을 바꾸지 않는다. 이 키 집합이 기존 asdict(TrackInfo) 결과다.
LEGACY_API_KEYS = {
    "name",
    "artist",
    "source_id",
    "album_art_url",
    "popularity",
    "match_score",
    "reverse_score",
    "algo",
    "label",
    "reason_tags",
}

ITUNES_ITEM = {
    "trackId": 1229073406,
    "trackName": "Through the Night",
    "artistName": "IU",
    "artworkUrl100": "https://example.com/100x100bb.jpg",
}

DEEZER_ITEM = {
    "id": 2215315187,
    "title": "Through the Night",
    "artist": {"name": "IU"},
    "album": {"cover_big": "https://example.com/dz.jpg"},
}


# ── 직렬화 계약 ───────────────────────────────────────────────────


def test_api_dict_keeps_legacy_key_set():
    payload = track_to_api_dict(TrackInfo(name="Lilac", artist="IU"))

    assert set(payload) == LEGACY_API_KEYS


def test_api_dict_does_not_leak_internal_identity_fields():
    """bindings와 recording_variant는 내부 표현이라 응답에 나오면 안 된다.

    provider별 ID 공개는 extension과의 계약이 정해진 뒤 별도로 다룬다.
    """
    track = TrackInfo(name="Lilac", artist="IU", recording_variant="acoustic")
    track.bind(_itunes_binding(ITUNES_ITEM))

    payload = track_to_api_dict(track)

    assert "bindings" not in payload
    assert "recording_variant" not in payload
    assert payload["source_id"] == "itunes:1229073406"


def test_api_dict_copies_reason_tags():
    """응답 dict를 고쳐도 원본 TrackInfo가 오염되지 않아야 한다."""
    track = TrackInfo(name="Lilac", artist="IU", reason_tags=["kpop"])

    track_to_api_dict(track)["reason_tags"].append("mutated")

    assert track.reason_tags == ["kpop"]


# ── binding 격리 ─────────────────────────────────────────────────


def test_providers_do_not_overwrite_each_other():
    """같은 곡의 iTunes ID와 Deezer ID가 동시에 남아야 한다.

    두 ID가 하나의 source_id 문자열을 두고 경합하던 것이 이 변경의 이유다.
    """
    track = TrackInfo(name="Through the Night", artist="IU")
    track.bind(_itunes_binding(ITUNES_ITEM))
    track.bind(_deezer_binding(DEEZER_ITEM))

    assert track.bindings["itunes"].provider_track_id == "1229073406"
    assert track.bindings["deezer"].provider_track_id == "2215315187"


def test_binding_order_does_not_change_representative_source_id():
    """대표 ID는 기록 순서가 아니라 우선순위(itunes -> deezer)가 정한다."""
    itunes_first = TrackInfo(name="Through the Night", artist="IU")
    itunes_first.bind(_itunes_binding(ITUNES_ITEM))
    itunes_first.bind(_deezer_binding(DEEZER_ITEM))

    deezer_first = TrackInfo(name="Through the Night", artist="IU")
    deezer_first.bind(_deezer_binding(DEEZER_ITEM))
    deezer_first.bind(_itunes_binding(ITUNES_ITEM))

    assert itunes_first.source_id == deezer_first.source_id == "itunes:1229073406"


def test_same_provider_rebinding_replaces_its_own_slot():
    track = TrackInfo(name="Through the Night", artist="IU")
    track.bind(_itunes_binding(ITUNES_ITEM))
    track.bind(_itunes_binding({**ITUNES_ITEM, "trackId": 999}))

    assert len(track.bindings) == 1
    assert track.source_id == "itunes:999"


def test_binding_records_provider_spelling_separately_from_canonical():
    """공급자 표기가 canonical과 달라도 canonical을 덮지 않는다.

    Deezer는 `밤편지`를 `Through the Night`로 싣는다. 어느 쪽이 어느
    카탈로그의 표기인지 남겨 두어야 preview 재검색을 없앨 수 있다.
    """
    track = TrackInfo(name="밤편지", artist="아이유")
    track.bind(_deezer_binding(DEEZER_ITEM))

    assert (track.name, track.artist) == ("밤편지", "아이유")
    assert track.bindings["deezer"].resolved_title == "Through the Night"
    assert track.bindings["deezer"].resolved_artist == "IU"


def test_track_without_binding_has_no_source_id():
    assert TrackInfo(name="Lilac", artist="IU").source_id is None


# ── 잘못된 응답은 binding을 만들지 않는다 ──────────────────────────

MALFORMED_ITUNES = [
    pytest.param({}, id="empty"),
    pytest.param({"trackName": "Hello", "artistName": "Adele"}, id="no-track-id"),
    pytest.param({"trackId": 0, "trackName": "Hello"}, id="zero-track-id"),
]

MALFORMED_DEEZER = [
    pytest.param({}, id="empty"),
    pytest.param({"title": "Hello", "artist": {"name": "Adele"}}, id="no-id"),
    pytest.param({"id": None, "title": "Hello"}, id="null-id"),
]


@pytest.mark.parametrize("item", MALFORMED_ITUNES)
def test_malformed_itunes_payload_produces_no_binding(item):
    assert _itunes_binding(item) is None


@pytest.mark.parametrize("item", MALFORMED_DEEZER)
def test_malformed_deezer_payload_produces_no_binding(item):
    assert _deezer_binding(item) is None


def test_deezer_binding_survives_artist_as_string():
    """artist가 dict가 아닌 응답에서도 터지지 않아야 한다."""
    binding = _deezer_binding({"id": 7, "title": "Hello", "artist": "Adele"})

    assert binding is not None
    assert binding.resolved_artist == "Adele"


def test_bind_ignores_none():
    track = TrackInfo(name="Lilac", artist="IU")
    track.bind(None)

    assert track.bindings == {}


# ── 기존 source_id 표기 호환 ──────────────────────────────────────


@pytest.mark.parametrize("source_id", ["itunes:1", "deezer:2", "lastfm:3"])
def test_source_id_round_trips(source_id):
    track = TrackInfo(name="Lilac", artist="IU")
    track.bind(binding_from_source_id(source_id))

    assert track.source_id == source_id


UNPARSEABLE_SOURCE_IDS = [
    pytest.param(None, id="none"),
    pytest.param("", id="empty"),
    pytest.param("1229073406", id="bare-number"),
    pytest.param("itunes:", id="no-id"),
    pytest.param("spotify:4", id="unknown-provider"),
]


@pytest.mark.parametrize("source_id", UNPARSEABLE_SOURCE_IDS)
def test_unparseable_source_id_produces_no_binding(source_id):
    assert binding_from_source_id(source_id) is None


# ── get_tracks_metadata의 공급자 호출 수 ──────────────────────────


class _CallCounter:
    """`fields` 조합별로 각 공급자가 몇 번 호출되는지 센다."""

    def __init__(self, monkeypatch, itunes=ITUNES_ITEM, deezer=DEEZER_ITEM):
        self.itunes_calls = 0
        self.deezer_calls = 0

        async def fake_itunes(http, track_name, artist="", **kwargs):
            self.itunes_calls += 1
            return itunes

        async def fake_deezer(http, track_name, artist):
            self.deezer_calls += 1
            return deezer

        monkeypatch.setattr(sources, "_itunes_or_none", fake_itunes)
        monkeypatch.setattr(sources, "_deezer_or_none", fake_deezer)


# (fields, iTunes 호출, Deezer 호출) — PR 1 이전과 같은 값이어야 한다.
CALL_BUDGET = [
    pytest.param(["source_id"], 1, 0, id="source_id"),
    pytest.param(["album_art"], 1, 0, id="album_art"),
    pytest.param(["popularity"], 0, 1, id="popularity"),
    pytest.param(["album_art", "source_id"], 1, 0, id="art+id"),
    pytest.param("all", 1, 1, id="all"),
]


@pytest.mark.parametrize("fields,want_itunes,want_deezer", CALL_BUDGET)
async def test_metadata_call_budget_per_field(
    monkeypatch, fields, want_itunes, want_deezer
):
    counter = _CallCounter(monkeypatch)
    track = TrackInfo(name="Through the Night", artist="IU")

    await get_tracks_metadata(None, [track], None, fields=fields)

    assert (counter.itunes_calls, counter.deezer_calls) == (want_itunes, want_deezer)


async def test_metadata_records_both_bindings_without_extra_calls(monkeypatch):
    """`all`은 두 공급자를 이미 호출하므로 두 binding을 모두 남길 수 있다.

    추가 호출 없이 얻는 정보이고, 대표 source_id는 우선순위가 정하므로 외부
    응답은 그대로다.
    """
    counter = _CallCounter(monkeypatch)
    track = TrackInfo(name="Through the Night", artist="IU")

    await get_tracks_metadata(None, [track], None, fields="all")

    assert (counter.itunes_calls, counter.deezer_calls) == (1, 1)
    assert set(track.bindings) == {"itunes", "deezer"}
    assert track.source_id == "itunes:1229073406"


async def test_metadata_falls_back_to_deezer_id_when_itunes_misses(monkeypatch):
    counter = _CallCounter(monkeypatch, itunes=None)
    track = TrackInfo(name="Through the Night", artist="IU")

    await get_tracks_metadata(None, [track], None, fields=["source_id"])

    # iTunes가 비면 Deezer를 한 번 더 부른다. 기존 동작과 같다.
    assert (counter.itunes_calls, counter.deezer_calls) == (1, 1)
    assert track.source_id == "deezer:2215315187"


async def test_metadata_leaves_no_binding_when_both_providers_miss(monkeypatch):
    _CallCounter(monkeypatch, itunes=None, deezer=None)
    track = TrackInfo(name="Nonexistent", artist="Nobody")

    await get_tracks_metadata(None, [track], None, fields=["source_id"])

    assert track.bindings == {}
    assert track_to_api_dict(track)["source_id"] is None
