"""
API 호출 계측 테스트.

Phase 1 (Lazy Enrichment) 적용 전에는 실패하는 것이 정상.
Phase 1 적용 후 전부 통과해야 한다.
"""
from recommend_algo import hidden_discovery, reverse_top100, similar_listening_pattern

TOP_N = 10
ENRICH_LIMIT_SIMILAR = TOP_N
ENRICH_LIMIT_REVERSE = TOP_N * 3
ENRICH_LIMIT_HIDDEN = TOP_N * 3


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


class FakeLfTrack:
    def __init__(self, similar_items):
        self._similar = similar_items

    def get_similar(self, limit=50):
        return self._similar[:limit]


class FakeSimilarArtistItem:
    def __init__(self, name, n_tracks=4):
        self._name = name
        self._n = n_tracks

    def get_name(self):
        return self._name

    def get_top_tracks(self, limit=10):
        class TR:
            def __init__(self_, artist, title):
                self_.item = FakeTrack(artist, title)

        return [TR(self._name, f"Track{i}") for i in range(min(limit, self._n))]


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
        return FakeLfTrack(self._tracks)

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
    enrich_counts = []

    async def counting_enrich(http, tracks):
        enrich_counts.append(len(tracks))
        for t in tracks:
            t.album_art_url = "https://example.com/art.jpg"
        return tracks

    monkeypatch.setattr("recommend_algo._enrich_metadata", counting_enrich)

    await similar_listening_pattern("Seed", "Artist", EmptyHttp(), lastfm, top_n=TOP_N)

    total = sum(enrich_counts)
    assert total <= ENRICH_LIMIT_SIMILAR, (
        f"similar_listening_pattern이 {total}개를 enrich함. 기대: <= {ENRICH_LIMIT_SIMILAR}"
    )


async def test_reverse_enrich_count_is_at_most_top_n_times_3(monkeypatch):
    """reverse_top100은 top_n * 3개 이하만 enrich해야 한다."""
    similar_tracks = [
        FakeSimilarResult(f"Artist{i}", f"Track{i}", 1.0 - i * 0.01) for i in range(80)
    ]
    similar_artists = [
        FakeSimilarArtistResult(FakeSimilarArtistItem(f"SimilarArtist{i}", 4), 0.8 - i * 0.1)
        for i in range(3)
    ]
    lastfm = CombinedFakeLastFm(similar_tracks, similar_artists)
    enrich_counts = []

    async def counting_enrich(http, tracks):
        enrich_counts.append(len(tracks))
        for t in tracks:
            t.album_art_url = "https://example.com/art.jpg"
        return tracks

    monkeypatch.setattr("recommend_algo._enrich_metadata", counting_enrich)

    await reverse_top100("Seed", "Artist", EmptyHttp(), lastfm, top_n=TOP_N)

    total = sum(enrich_counts)
    assert total <= ENRICH_LIMIT_REVERSE, (
        f"reverse_top100이 {total}개를 enrich함. 기대: <= {ENRICH_LIMIT_REVERSE}"
    )


async def test_hidden_enrich_count_is_at_most_top_n_times_3(monkeypatch):
    """hidden_discovery는 top_n * 3개 이하만 enrich해야 한다."""
    similar_artists = [
        FakeSimilarArtistResult(FakeSimilarArtistItem(f"Artist{i}", 4), 0.9 - i * 0.03)
        for i in range(30)
    ]
    lastfm = HiddenFakeLastFm(similar_artists)
    enrich_counts = []

    async def counting_enrich(http, tracks):
        enrich_counts.append(len(tracks))
        for t in tracks:
            t.album_art_url = "https://example.com/art.jpg"
        return tracks

    monkeypatch.setattr("recommend_algo._enrich_metadata", counting_enrich)

    await hidden_discovery("Seed", "Artist", EmptyHttp(), lastfm, top_n=TOP_N)

    total = sum(enrich_counts)
    assert total <= ENRICH_LIMIT_HIDDEN, (
        f"hidden_discovery가 {total}개를 enrich함. 기대: <= {ENRICH_LIMIT_HIDDEN}"
    )
