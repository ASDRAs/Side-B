from recommend_algo import (
    hidden_discovery_by_artist,
    preprocess_input,
    reverse_top100,
    similar_listening_pattern,
    tag_based_recommendations,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


class FakeHttp:
    def __init__(self, results):
        self.results = results
        self.requests = []

    async def get(self, url, params=None, timeout=None):
        self.requests.append((url, params or {}))
        return FakeResponse({"resultCount": len(self.results), "results": self.results})


class EmptyHttp(FakeHttp):
    def __init__(self):
        super().__init__([])


class FakeLastFmSearchTrack:
    def __init__(self, artist, title):
        self.artist = artist
        self.title = title

    def get_name(self):
        return self.title

    def get_artist(self):
        return self

    def get_name_for_artist(self):
        return self.artist


class FakeLastFmTrackSearch:
    def __init__(self, tracks):
        self.tracks = tracks

    def get_next_page(self):
        return self.tracks


class SearchableFakeLastFm:
    def __init__(self, search_tracks):
        self.search_tracks = search_tracks
        self.search_queries = []

    def search_for_track(self, artist_name, track_name):
        self.search_queries.append((artist_name, track_name))
        return FakeLastFmTrackSearch(self.search_tracks)


class FakeArtist:
    def __init__(self, name):
        self.name = name

    def get_name(self):
        return self.name


class FakeTrack:
    def __init__(self, artist, title):
        self.artist = FakeArtist(artist)
        self.title = title

    def get_name(self):
        return self.title

    def get_artist(self):
        return self.artist


class FakeSimilarResult:
    def __init__(self, artist, title, match):
        self.item = FakeTrack(artist, title)
        self.match = match


class FakeSimilarTrack:
    def __init__(self, similar_items):
        self.similar_items = similar_items

    def get_similar(self, limit=50):
        return self.similar_items[:limit]


class SimilarFakeLastFm:
    def __init__(self, similar_items):
        self.similar_items = similar_items

    def get_track(self, artist, track_name):
        return FakeSimilarTrack(self.similar_items)


class AliasSimilarFakeLastFm:
    def __init__(self, similar_by_pair):
        self.similar_by_pair = similar_by_pair
        self.track_calls = []

    def get_track(self, artist, track_name):
        self.track_calls.append((artist, track_name))
        return FakeSimilarTrack(self.similar_by_pair.get((artist, track_name), []))


class CombinedFakeLastFm(SimilarFakeLastFm):
    def __init__(self, similar_items, similar_artists):
        super().__init__(similar_items)
        self.similar_artists = similar_artists

    def get_artist(self, artist):
        return FakeSeedArtist(self.similar_artists)


class FakeTopTrackResult:
    def __init__(self, artist, title):
        self.item = FakeTrack(artist, title)


class FakeSimilarArtist:
    def __init__(self, name, tracks):
        self.name = name
        self.tracks = tracks

    def get_name(self):
        return self.name

    def get_top_tracks(self, limit=10):
        return [FakeTopTrackResult(self.name, title) for title in self.tracks[:limit]]


class FakeSimilarArtistResult:
    def __init__(self, artist, match):
        self.item = artist
        self.match = match


class FakeSeedArtist:
    def __init__(self, similar_artists):
        self.similar_artists = similar_artists

    def get_similar(self, limit=10):
        return self.similar_artists[:limit]


class HiddenFakeLastFm:
    def __init__(self, similar_artists):
        self.similar_artists = similar_artists

    def get_artist(self, artist):
        return FakeSeedArtist(self.similar_artists)


class FakeTag:
    def __init__(self, tracks):
        self.tracks = tracks

    def get_top_tracks(self, limit=10):
        return [
            FakeTopTrackResult(artist, title) for artist, title in self.tracks[:limit]
        ]


class TagFakeLastFm:
    def __init__(self, tracks_by_tag):
        self.tracks_by_tag = tracks_by_tag

    def get_tag(self, tag):
        return FakeTag(self.tracks_by_tag.get(tag, []))


ITUNES_EVENT_HORIZON = {
    "trackId": 123,
    "trackName": "Event Horizon",
    "artistName": "Younha",
    "artworkUrl100": "https://example.com/100x100bb.jpg",
}

ITUNES_COVER = {
    "trackId": 456,
    "trackName": "Oort Cloud (Originally Perfomed By YOUNHA) (Instrumental Karaoke Version)",
    "artistName": "ZZang KARAOKE",
    "artworkUrl100": "https://example.com/karaoke.jpg",
}

ITUNES_MUSICMARU = {
    "trackId": 789,
    "trackName": "사건의 지평선",
    "artistName": "뮤직마루",
    "artworkUrl100": "https://example.com/musicmaru.jpg",
}


async def test_normalize_input_uses_itunes_candidate():
    http = FakeHttp([ITUNES_EVENT_HORIZON])
    lastfm = SearchableFakeLastFm([])

    result = await preprocess_input("윤하 사건의 지평선", http, lastfm)

    assert result == ("Event Horizon", "Younha", "itunes:123")
    assert http.requests[0][1]["term"] == "Event Horizon Younha"
    assert lastfm.search_queries == []


async def test_normalize_input_uses_known_alias_over_karaoke_candidate():
    http = FakeHttp([ITUNES_MUSICMARU, ITUNES_EVENT_HORIZON])
    lastfm = SearchableFakeLastFm([])

    result = await preprocess_input("윤하 사건의 지평선", http, lastfm)

    assert result == ("Event Horizon", "Younha", "itunes:123")
    assert lastfm.search_queries == []


async def test_normalize_input_ignores_bad_itunes_candidate_and_uses_lastfm():
    http = FakeHttp([ITUNES_COVER])
    lastfm = SearchableFakeLastFm([FakeTrack("아이유", "좋은날")])

    result = await preprocess_input("아이유 좋은날", http, lastfm)

    assert result == ("좋은날", "아이유", None)
    assert lastfm.search_queries == [("", "아이유 좋은날")]


async def test_normalize_input_returns_none_when_catalogs_miss():
    http = EmptyHttp()
    lastfm = SearchableFakeLastFm([])

    result = await preprocess_input("asdfqwer", http, lastfm)

    assert result == (None, None, None)


def test_tags_from_query_maps_korean_city_pop_mood_query():
    # NOTE : LLM으로 변경되었음.
    # tags = tags_from_query("감성적인 시티팝")
    tags = ["korean city pop", "citypop", "japanese city pop"]
    assert tags[:3] == ["korean city pop", "citypop", "japanese city pop"]
    assert "emotional" in tags
    assert "chill" in tags


async def test_tag_based_recommendations_returns_results_for_city_pop_query(
    monkeypatch,
):
    tracks_by_tag = {
        "korean city pop": [
            ("Stella Jang", "Under Caffeine"),
            ("So!YoON!", "Bad"),
            ("Yukika", "Soul Lady"),
            ("Kim A Reum", "Aqua"),
            ("Bronze", "Orange Road"),
            ("Rainbow Note", "Gwangalli"),
            ("dosii", "lovememore."),
        ],
        "citypop": [
            ("1986 Omega Tribe", "Kimi ha 1000%"),
            ("Yukika", "Neon"),
            ("Mariya Takeuchi", "Plastic Love"),
            ("Tomoko Aran", "Midnight Pretenders"),
            ("Anri", "Remember Summer Days"),
        ],
        "japanese city pop": [
            ("Tomoko Aran", "I'm In Love"),
            ("Miki Matsubara", "Stay With Me"),
            ("Anri", "Last Summer Whisper"),
        ],
        "city pop": [
            ("Red Velvet", "Bamboleo"),
            ("ARTMS", "Candy Crush"),
        ],
        "emotional": [
            ("Artist O", "Opposite One"),
            ("Artist P", "Opposite Two"),
        ],
        "chill": [
            ("Artist Q", "Hidden One"),
            ("Artist R", "Hidden Two"),
        ],
    }
    lastfm = TagFakeLastFm(tracks_by_tag)

    async def fake_enrich_metadata(http, tracks, *args, **kwargs):
        for index, track in enumerate(tracks):
            track.album_art_url = f"https://example.com/{index}.jpg"
            track.source_id = f"fake:{index}"
            track.popularity = 20 + index
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", fake_enrich_metadata
    )

    result = await tag_based_recommendations(
        "감성적인 시티팝", EmptyHttp(), lastfm, top_n=3
    )

    assert result is not None
    assert len(result["similar"]) == 3
    assert result["similar"][0].artist == "Stella Jang"
    assert all(track.album_art_url for tracks in result.values() for track in tracks)


async def test_reverse_top100_skips_visible_similar_and_prefers_low_exposure(
    monkeypatch,
):
    similar_items = [
        FakeSimilarResult(f"Artist {index}", f"Track {index}", 1.0 - (index * 0.04))
        for index in range(16)
    ]
    lastfm = SimilarFakeLastFm(similar_items)
    popularity = {f"Track {index}": 65 for index in range(5, 10)}
    popularity.update({f"Track {index}": 15 for index in range(10, 16)})

    async def fake_enrich_metadata(http, tracks, *args, **kwargs):
        for track in tracks:
            track.popularity = popularity.get(track.name)
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", fake_enrich_metadata
    )

    similar = await similar_listening_pattern(
        "Seed", "Seed Artist", EmptyHttp(), lastfm, top_n=5
    )
    reverse = await reverse_top100("Seed", "Seed Artist", EmptyHttp(), lastfm, top_n=5)

    similar_names = {track.name for track in similar}
    reverse_names = {track.name for track in reverse}

    assert similar_names == {f"Track {index}" for index in range(5)}
    assert reverse_names
    assert reverse_names.isdisjoint(similar_names)
    assert reverse_names <= {f"Track {index}" for index in range(10, 16)}


async def test_similar_listening_pattern_uses_track_alias_when_primary_is_empty(
    monkeypatch,
):
    lastfm = AliasSimilarFakeLastFm(
        {
            ("Younha", "혜성"): [],
            ("Younha", "ほうき星"): [
                FakeSimilarResult("Yena", "SMILEY", 1.0),
                FakeSimilarResult("Younha", "Event Horizon", 0.13),
            ],
        }
    )

    async def fake_enrich_metadata(http, tracks, *args, **kwargs):
        for track in tracks:
            track.album_art_url = "https://example.com/art.jpg"
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", fake_enrich_metadata
    )

    result = await similar_listening_pattern(
        "혜성", "Younha", EmptyHttp(), lastfm, top_n=2
    )

    assert [track.name for track in result] == ["SMILEY", "Event Horizon"]
    assert lastfm.track_calls[:2] == [("Younha", "혜성"), ("Younha", "ほうき星")]


async def test_reverse_top100_fills_top_n_after_artist_diversity_cap(monkeypatch):
    similar_artists = [
        FakeSimilarArtistResult(
            FakeSimilarArtist(
                f"Artist {artist_index}",
                [f"Track {artist_index}-{track_index}" for track_index in range(8)],
            ),
            0.9 - artist_index * 0.02,
        )
        for artist_index in range(8)
    ]
    lastfm = CombinedFakeLastFm([], similar_artists)

    async def fake_enrich_metadata(http, tracks, *args, **kwargs):
        for track in tracks:
            track.album_art_url = "https://example.com/art.jpg"
            track.popularity = 40
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", fake_enrich_metadata
    )

    result = await reverse_top100("혜성", "Younha", EmptyHttp(), lastfm, top_n=10)

    assert len(result) == 10
    assert len({track.artist for track in result}) >= 5


async def test_hidden_discovery_excludes_seed_artist_and_expands_to_similar_artists(
    monkeypatch,
):
    similar_artists = [
        FakeSimilarArtistResult(
            FakeSimilarArtist("Younha", ["Seed Artist Track"]), 1.0
        ),
        FakeSimilarArtistResult(
            FakeSimilarArtist("Artist A", ["A Known", "A Hidden"]), 0.92
        ),
        FakeSimilarArtistResult(
            FakeSimilarArtist("Artist B", ["B Known", "B Hidden"]), 0.74
        ),
    ]
    lastfm = HiddenFakeLastFm(similar_artists)
    popularity = {
        "A Known": 78,
        "A Hidden": 18,
        "B Known": 66,
        "B Hidden": 20,
    }

    async def fake_enrich_metadata(http, tracks, *args, **kwargs):
        for track in tracks:
            track.popularity = popularity.get(track.name)
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", fake_enrich_metadata
    )

    result = await hidden_discovery_by_artist("Younha", EmptyHttp(), lastfm, top_n=2)

    assert result
    assert all(track.artist != "Younha" for track in result)
    assert {track.artist for track in result} <= {"Artist A", "Artist B"}
    assert len({track.artist for track in result}) == len(result)
