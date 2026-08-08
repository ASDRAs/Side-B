"""
API 호출 계측 테스트.

Phase 1 (Lazy Enrichment) 적용 전에는 실패하는 것이 정상.
Phase 1 적용 후 전부 통과해야 한다.
"""

from recommend_algo import (
    hidden_discovery_by_artist,
    reverse_top100,
    similar_listening_pattern,
)
from tests.lastfm_fakes import ArtistTopTracksSource, SimilarTrackSource

TOP_N = 10
ENRICH_LIMIT_SIMILAR = TOP_N
FINAL_METADATA_LIMIT = TOP_N


# ── 공통 Fake 인프라 ──────────────────────────────────────────────


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


class EmptyHttp:
    async def get(self, url, params=None, timeout=None):
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


async def test_similar_enrich_count_is_at_most_top_n(monkeypatch):
    """similar_listening_pattern은 top_n개 이하만 enrich해야 한다."""
    similar_tracks = [
        FakeSimilarResult(f"Artist{i}", f"Track{i}", 1.0 - i * 0.01) for i in range(50)
    ]
    lastfm = CombinedFakeLastFm(similar_tracks)
    metadata_calls = []

    async def counting_enrich(http, tracks, *args, **kwargs):
        metadata_calls.append((tuple(kwargs.get("fields") or ()), len(tracks)))
        for t in tracks:
            t.album_art_url = "https://example.com/art.jpg"
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", counting_enrich
    )

    await similar_listening_pattern("Seed", "Artist", EmptyHttp(), lastfm, top_n=TOP_N)

    total = sum(count for _, count in metadata_calls)
    assert total <= ENRICH_LIMIT_SIMILAR, (
        f"similar_listening_pattern이 {total}개를 enrich함. 기대: <= {ENRICH_LIMIT_SIMILAR}"
    )


async def test_reverse_enrich_count_is_at_most_top_n_times_3(monkeypatch):
    """reverse_top100은 top_n * 3개 이하만 enrich해야 한다."""
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

    await reverse_top100("Seed", "Artist", EmptyHttp(), lastfm, top_n=TOP_N)

    popularity_total = sum(
        count for fields, count in metadata_calls if fields == ("popularity",)
    )
    final_total = sum(
        count
        for fields, count in metadata_calls
        if fields == ("album_art", "source_id")
    )
    assert popularity_total == 0, (
        f"reverse_top100이 {popularity_total}개를 popularity enrich함. "
        "노출도는 Last.fm 응답에서 계산하므로 이 fan-out은 0이어야 한다."
    )
    assert final_total <= FINAL_METADATA_LIMIT, (
        f"reverse_top100이 {final_total}개를 최종 metadata enrich함. "
        f"기대: <= {FINAL_METADATA_LIMIT}"
    )


async def test_hidden_enrich_count_is_at_most_top_n_times_3(monkeypatch):
    """hidden_discovery는 top_n * 3개 이하만 enrich해야 한다."""
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

    await hidden_discovery_by_artist("Artist", EmptyHttp(), lastfm, top_n=TOP_N)

    popularity_total = sum(
        count for fields, count in metadata_calls if fields == ("popularity",)
    )
    final_total = sum(
        count
        for fields, count in metadata_calls
        if fields == ("album_art", "source_id")
    )
    assert popularity_total == 0, (
        f"hidden_discovery가 {popularity_total}개를 popularity enrich함. "
        "노출도는 Last.fm 응답에서 계산하므로 이 fan-out은 0이어야 한다."
    )
    assert final_total <= FINAL_METADATA_LIMIT, (
        f"hidden_discovery가 {final_total}개를 최종 metadata enrich함. "
        f"기대: <= {FINAL_METADATA_LIMIT}"
    )
