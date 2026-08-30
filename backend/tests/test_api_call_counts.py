"""
API 호출 계측 테스트.

추천 생성은 후보에 대해 공급자 metadata를 조회하지 않는다. 곡마다 iTunes를
부르면 추천 한 번에 30회가 나가는데 상한이 분당 20회라, 사용자 한 명이 한 번
검색하는 것만으로 제한을 넘긴다. 앨범아트와 ID는 preview 클릭 시점에 채운다.

이 파일은 그 계약을 고정한다. 여기 숫자가 0이 아니게 되면 fan-out이 되살아난
것이다.
"""

from app.llm.llm_response import OppositeTagAnalysis
from recommend_algo import (
    hidden_discovery_by_artist,
    opposite_emotion,
    reverse_top100,
    similar_listening_pattern,
)
from tests.lastfm_fakes import ArtistTopTracksSource, SimilarTrackSource

TOP_N = 10


# ── 공통 Fake 인프라 ──────────────────────────────────────────────


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


class EmptyHttp:
    """빈 응답만 주는 http 대역. 어떤 URL이 호출됐는지 기록한다."""

    def __init__(self):
        self.calls = []

    async def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        return FakeResponse({"resultCount": 0, "results": []})


class FakeArtist:
    def __init__(self, name):
        self.name = name

    def get_name(self):
        return self.name


class FakeTrack:
    def __init__(self, artist, title):
        self._artist = FakeArtist(artist)
        self._title = title

    def get_name(self):
        return self._title

    def get_artist(self):
        return self._artist


class FakeSimilarResult:
    def __init__(self, artist, title, match=0.5):
        self.item = FakeTrack(artist, title)
        self.match = match


class FakeSimilarArtistItem(ArtistTopTracksSource):
    def __init__(self, name, n_tracks=4):
        super().__init__(name, [f"Track{i}" for i in range(n_tracks)])


class FakeSimilarArtistResult:
    def __init__(self, artist_item, match=0.5):
        self.item = artist_item
        self.match = match


class FakeLfArtist:
    def __init__(self, similar_artists):
        self._similar = similar_artists

    def get_similar(self, limit=10):
        return self._similar[:limit]


class CombinedFakeLastFm:
    """similar_listening_pattern + reverse_top100 모두 지원."""

    def __init__(self, similar_tracks, similar_artists=None):
        self._tracks = similar_tracks
        self._artists = similar_artists or []

    def get_track(self, artist, name):
        return SimilarTrackSource(self._tracks, artist, name)

    def get_artist(self, artist):
        return FakeLfArtist(self._artists)


class HiddenFakeLastFm:
    def __init__(self, similar_artists):
        self._similar = similar_artists

    def get_artist(self, artist):
        return FakeLfArtist(self._similar)


# ── 계측 테스트 ───────────────────────────────────────────────────


async def test_similar_makes_no_candidate_metadata_fanout(monkeypatch):
    """similar_listening_pattern은 후보 metadata를 조회하지 않는다."""
    similar_tracks = [
        FakeSimilarResult(f"Artist{i}", f"Track{i}", 1.0 - i * 0.01) for i in range(50)
    ]
    lastfm = CombinedFakeLastFm(similar_tracks)
    metadata_calls = []

    async def counting_enrich(http, tracks, *args, **kwargs):
        metadata_calls.append(len(tracks))
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", counting_enrich
    )

    http = EmptyHttp()
    await similar_listening_pattern("Seed", "Artist", http, lastfm, top_n=TOP_N)

    assert metadata_calls == [], (
        f"similar_listening_pattern이 후보 {sum(metadata_calls)}개를 enrich함. "
        "후보 fan-out은 0이어야 한다."
    )
    assert http.calls == [], f"공급자 직접 호출이 남아 있다: {http.calls}"


async def test_reverse_makes_no_candidate_metadata_fanout(monkeypatch):
    """reverse_top100은 후보 metadata를 조회하지 않는다."""
    similar_tracks = [
        FakeSimilarResult(f"Artist{i}", f"Track{i}", 1.0 - i * 0.01) for i in range(80)
    ]
    similar_artists = [
        FakeSimilarArtistResult(
            FakeSimilarArtistItem(f"SimilarArtist{i}", 4), 0.8 - i * 0.1
        )
        for i in range(3)
    ]
    lastfm = CombinedFakeLastFm(similar_tracks, similar_artists)
    metadata_calls = []

    async def counting_enrich(http, tracks, *args, **kwargs):
        metadata_calls.append((tuple(kwargs.get("fields") or ()), len(tracks)))
        for t in tracks:
            t.album_art_url = "https://example.com/art.jpg"
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", counting_enrich
    )

    http = EmptyHttp()
    await reverse_top100("Seed", "Artist", http, lastfm, top_n=TOP_N)

    assert metadata_calls == [], (
        f"reverse_top100이 후보 {sum(count for _, count in metadata_calls)}개를 "
        "enrich함. popularity fan-out도 최종 metadata fan-out도 0이어야 한다."
    )
    assert http.calls == [], f"공급자 직접 호출이 남아 있다: {http.calls}"


async def test_hidden_makes_no_candidate_metadata_fanout(monkeypatch):
    """hidden_discovery는 후보 metadata를 조회하지 않는다."""
    similar_artists = [
        FakeSimilarArtistResult(FakeSimilarArtistItem(f"Artist{i}", 4), 0.9 - i * 0.03)
        for i in range(30)
    ]
    lastfm = HiddenFakeLastFm(similar_artists)
    metadata_calls = []

    async def counting_enrich(http, tracks, *args, **kwargs):
        metadata_calls.append((tuple(kwargs.get("fields") or ()), len(tracks)))
        for t in tracks:
            t.album_art_url = "https://example.com/art.jpg"
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", counting_enrich
    )

    http = EmptyHttp()
    await hidden_discovery_by_artist("Artist", http, lastfm, top_n=TOP_N)

    assert metadata_calls == [], (
        f"hidden_discovery가 후보 {sum(count for _, count in metadata_calls)}개를 "
        "enrich함. popularity fan-out도 최종 metadata fan-out도 0이어야 한다."
    )
    assert http.calls == [], f"공급자 직접 호출이 남아 있다: {http.calls}"


# ── opposite ────────────────────────────────────────────────────
#
# 822209f가 나머지 알고리즘에서 fan-out을 걷어낼 때 opposite만 빠졌고, 이 파일에
# opposite 케이스가 없어서 회귀가 드러나지 않았다. 규칙에 적용 범위가 없으면
# 테스트가 잡아주지 못한다.


class _FakeTopTrack:
    def __init__(self, artist, title):
        self.item = _FakeTrackItem(artist, title)


class _FakeTrackItem:
    def __init__(self, artist, title):
        self._artist = artist
        self._title = title

    def get_name(self):
        return self._title

    def get_artist(self):
        return _FakeArtist(self._artist)


class _FakeArtist:
    def __init__(self, name):
        self._name = name

    def get_name(self):
        return self._name


class _FakeTag:
    def __init__(self, tracks):
        self.tracks = tracks

    def get_top_tracks(self, limit=10):
        return [_FakeTopTrack(artist, title) for artist, title in self.tracks[:limit]]


class _TagFakeLastFm:
    def __init__(self, tracks_by_tag):
        self.tracks_by_tag = tracks_by_tag

    def get_tag(self, tag):
        return _FakeTag(self.tracks_by_tag.get(tag, []))


class _FakeGemini:
    def request(self, **kwargs):
        return OppositeTagAnalysis(opposite_tags=["bright"])


async def test_opposite_makes_no_candidate_metadata_fanout(monkeypatch):
    """opposite_emotion도 후보 metadata를 조회하지 않는다."""
    lastfm = _TagFakeLastFm(
        {"bright": [(f"Artist{i}", f"Track{i}") for i in range(50)]}
    )
    metadata_calls = []

    async def counting_enrich(http, tracks, *args, **kwargs):
        metadata_calls.append(len(tracks))
        return tracks

    async def fake_seed_tags(*args, **kwargs):
        return ["calm"]

    monkeypatch.setattr("recommend_algo.algorithms.opposite._seed_tags", fake_seed_tags)
    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", counting_enrich
    )

    http = EmptyHttp()
    result = await opposite_emotion(
        "Seed",
        "Seed Artist",
        http,
        lastfm,
        gemini_wrapper=_FakeGemini(),
        top_n=TOP_N,
    )

    assert result, "후보가 비면 fan-out 0을 확인할 수 없다."
    assert metadata_calls == [], (
        f"opposite_emotion이 후보 {sum(metadata_calls)}개를 enrich함. "
        "후보 fan-out은 0이어야 한다."
    )
    assert http.calls == [], f"공급자 직접 호출이 남아 있다: {http.calls}"
