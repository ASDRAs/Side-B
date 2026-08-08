from collections import Counter

import pytest

from app.llm.llm_response import AlternativeQuery, MoodAnalysis, OppositeTagAnalysis
from app.utils.text import compact_text
from recommend_algo import (
    hidden_discovery_by_artist,
    opposite_emotion,
    preprocess_input,
    reverse_top100,
    similar_listening_pattern,
    tag_based_recommendations,
)
from recommend_algo.common import scoring, seeds
from recommend_algo.common.models import ProviderBinding, TrackInfo
from tests.lastfm_fakes import ArtistTopTracksSource, SimilarTrackSource


def test_mood_analysis_allows_missing_opposite_tags():
    analysis = MoodAnalysis(tags=["calm"])

    assert analysis.opposite_tags == []


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


class SimilarFakeLastFm:
    def __init__(self, similar_items):
        self.similar_items = similar_items

    def get_track(self, artist, track_name):
        return SimilarTrackSource(self.similar_items, artist, track_name)


class AliasSimilarFakeLastFm:
    def __init__(self, similar_by_pair):
        self.similar_by_pair = similar_by_pair
        self.track_calls = []

    def get_track(self, artist, track_name):
        self.track_calls.append((artist, track_name))
        return SimilarTrackSource(
            self.similar_by_pair.get((artist, track_name), []), artist, track_name
        )


class CombinedFakeLastFm(SimilarFakeLastFm):
    def __init__(self, similar_items, similar_artists):
        super().__init__(similar_items)
        self.similar_artists = similar_artists

    def get_artist(self, artist):
        return FakeSeedArtist(self.similar_artists)


class FakeTopTrackResult:
    def __init__(self, artist, title):
        self.item = FakeTrack(artist, title)


class FakeSimilarArtist(ArtistTopTracksSource):
    """artist.getTopTracks XML을 내는 유사 아티스트."""

    def __init__(self, name, tracks):
        super().__init__(name, tracks)


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
        self.tag_queries = []

    def get_tag(self, tag):
        self.tag_queries.append(tag)
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


def _alt(track_title, artist_name):
    return AlternativeQuery(track_title=track_title, artist_name=artist_name)


async def test_normalize_input_uses_itunes_candidate():
    """원어 표기가 카탈로그와 어긋나면 영문 대체 표기가 곡을 확정한다."""
    http = FakeHttp([ITUNES_EVENT_HORIZON])
    lastfm = SearchableFakeLastFm([])

    result = await preprocess_input(
        "윤하 사건의 지평선",
        [_alt("Event Horizon", "Younha")],
        http,
        lastfm,
        track_title="사건의 지평선",
        artist_name="윤하",
    )

    assert result == ("Event Horizon", "Younha", "itunes:123")
    # 표기별로 채점하지 않고 alias 전체로 채점하므로 첫 조회에서 완전 일치가 난다.
    assert [request[1]["term"] for request in http.requests] == ["사건의 지평선 윤하"]
    assert lastfm.search_queries == []


async def test_normalize_input_uses_known_alias_over_karaoke_candidate():
    http = FakeHttp([ITUNES_MUSICMARU, ITUNES_EVENT_HORIZON])
    lastfm = SearchableFakeLastFm([])

    result = await preprocess_input(
        "윤하 사건의 지평선",
        [_alt("Event Horizon", "Younha")],
        http,
        lastfm,
        track_title="사건의 지평선",
        artist_name="윤하",
    )

    assert result == ("Event Horizon", "Younha", "itunes:123")
    assert lastfm.search_queries == []


async def test_normalize_input_ignores_bad_itunes_candidate_and_uses_lastfm():
    http = FakeHttp([ITUNES_COVER])
    lastfm = SearchableFakeLastFm([FakeTrack("아이유", "좋은날")])

    result = await preprocess_input("아이유 좋은날", [], http, lastfm)

    assert result == ("좋은날", "아이유", None)
    assert lastfm.search_queries == [("", "아이유 좋은날")]


ITUNES_SPRING_DAY_IMPOSTOR = {
    "trackId": 111,
    "trackName": "봄날",
    "artistName": "The Hit Crew",
    "artworkUrl100": "https://example.com/hitcrew.jpg",
}

ITUNES_SPRING_DAY = {
    "trackId": 222,
    "trackName": "봄날",
    "artistName": "BTS",
    "artworkUrl100": "https://example.com/springday.jpg",
}


async def test_normalize_input_ranks_by_artist_when_llm_splits_title_and_artist():
    """아티스트를 넘기지 않으면 두 후보의 점수가 같아 첫 결과(오답)가 뽑힌다."""
    http = FakeHttp([ITUNES_SPRING_DAY_IMPOSTOR, ITUNES_SPRING_DAY])
    lastfm = SearchableFakeLastFm([])

    result = await preprocess_input(
        "봄날 BTS",
        [],
        http,
        lastfm,
        track_title="봄날",
        artist_name="BTS",
    )

    assert result == ("봄날", "BTS", "itunes:222")
    assert http.requests[0][1]["term"] == "봄날 BTS"


# 실측한 오답 기준곡들. 전부 제목은 정확히 맞고 아티스트만 다르며,
# 총점만으로는 0.62를 넘기 때문에 아티스트 하한 없이는 통과한다.
WRONG_ARTIST_CANDIDATES = [
    pytest.param("Through the Night", "Slushii & Hatsune Miku", id="hatsune-miku"),
    pytest.param("Through the Night", "Shin Giwon", id="shin-giwon"),
    pytest.param("Through the Night", "TOMSSON", id="tomsson"),
    pytest.param("You & I", "John Legend", id="john-legend"),
    pytest.param("만약에 (태연)", "Yoon Min Soo", id="yoon-min-soo"),
]


@pytest.mark.parametrize("title,artist", WRONG_ARTIST_CANDIDATES)
async def test_normalize_input_rejects_title_match_with_wrong_artist(title, artist):
    http = FakeHttp(
        [{"trackId": 900, "trackName": title, "artistName": artist}],
    )
    lastfm = SearchableFakeLastFm([])

    result = await preprocess_input(
        "아이유 밤편지",
        [_alt("Through the Night", "IU")],
        http,
        lastfm,
        track_title="밤편지",
        artist_name="아이유",
    )

    assert result == (None, None, None)


async def test_normalize_input_returns_unresolved_instead_of_loose_guess():
    """제목·아티스트를 모두 아는 요청은 느슨한 문자열 검색으로 내려가지 않는다."""
    http = FakeHttp([{"trackId": 901, "trackName": "Iu", "artistName": "TOMSSON"}])
    lastfm = SearchableFakeLastFm([])

    result = await preprocess_input(
        "아이유 밤편지",
        [_alt("Through the Night", "IU")],
        http,
        lastfm,
        track_title="밤편지",
        artist_name="아이유",
    )

    assert result == (None, None, None)
    # 원표기와 대체표기만 조회하고, 결합 문자열은 시도하지 않는다.
    assert [request[1]["term"] for request in http.requests] == [
        "밤편지 아이유",
        "Through the Night IU",
    ]


async def test_normalize_input_stops_at_exact_match():
    """완전 일치가 나오면 남은 대체 표기를 조회하지 않는다."""
    http = FakeHttp(
        [{"trackId": 902, "trackName": "Through the Night", "artistName": "IU"}]
    )
    lastfm = SearchableFakeLastFm([])

    result = await preprocess_input(
        "아이유 밤편지",
        [_alt("Through the Night", "IU"), _alt("Bam Pyeon Ji", "IU")],
        http,
        lastfm,
        track_title="Through the Night",
        artist_name="IU",
    )

    assert result == ("Through the Night", "IU", "itunes:902")
    assert len(http.requests) == 1


# 카탈로그가 제목과 아티스트의 표기 언어를 섞어 등록한 실제 레코드들.
# 제목은 한쪽 표기와, 아티스트는 다른 쪽 표기와 맞으므로 (제목, 아티스트) 쌍
# 단위로 비교하면 어느 쌍에서도 통과하지 못한다.
# (카탈로그 제목, 카탈로그 아티스트, 원표기 pair, 대체표기 pair)
MIXED_LANGUAGE_RECORDS = [
    pytest.param(
        "너랑 나 (YOU&I)",
        "IU",
        ("너랑 나", "아이유"),
        ("You & I", "IU"),
        id="ko-title-en-artist",
    ),
    pytest.param(
        "사건의 지평선",
        "Younha",
        ("사건의 지평선", "윤하"),
        ("Event Horizon", "Younha"),
        id="ko-title-romanized-artist",
    ),
    pytest.param(
        "人生のメリーゴーランド",
        "Joe Hisaishi",
        ("人生のメリーゴーランド", "久石譲"),
        ("Merry-Go-Round of Life", "Joe Hisaishi"),
        id="ja-title-en-artist",
    ),
    pytest.param(
        "Through the Night",
        "아이유",
        ("밤편지", "아이유"),
        ("Through the Night", "IU"),
        id="en-title-ko-artist",
    ),
]


@pytest.mark.parametrize(
    "catalog_title,catalog_artist,primary,alternative", MIXED_LANGUAGE_RECORDS
)
async def test_mixed_language_catalog_entry_resolves(
    catalog_title, catalog_artist, primary, alternative
):
    http = FakeHttp(
        [{"trackId": 903, "trackName": catalog_title, "artistName": catalog_artist}]
    )
    lastfm = SearchableFakeLastFm([])

    result = await preprocess_input(
        "query",
        [_alt(*alternative)],
        http,
        lastfm,
        track_title=primary[0],
        artist_name=primary[1],
    )

    assert result == (catalog_title, catalog_artist, "itunes:903")


def test_mixed_language_seed_reaches_lastfm_similar_alias():
    """확정된 기준곡이 Last.fm 유사곡 조회용 alias 테이블에 걸려야 한다."""
    key = (compact_text("IU"), compact_text("너랑 나 (YOU&I)"))

    assert key in seeds._TRACK_SIMILAR_ALIASES
    assert ("You&I", "IU") in seeds._TRACK_SIMILAR_ALIASES[key]


async def test_track_similar_keeps_punctuation_variant_alias():
    """Last.fm 실측: "너랑 나 (YOU&I)" 0개, "You & I" 0개, "You&I" 5개.

    문장부호만 다른 표기를 중복으로 접으면 유일하게 결과가 나오는 표기가
    사라진다. 중복 제거 키와 캐시 키 양쪽 모두 문장부호를 보존해야 한다.
    """
    lastfm = AliasSimilarFakeLastFm(
        {("IU", "You&I"): [FakeSimilarResult("IU", "Palette", 0.9)]}
    )

    result = await seeds._track_similar_tracks("너랑 나 (YOU&I)", "IU", lastfm, 5)

    assert lastfm.track_calls == [
        ("IU", "너랑 나 (YOU&I)"),
        ("IU", "You & I"),
        ("IU", "You&I"),
    ]
    assert [item.item.get_name() for item in result] == ["Palette"]


async def test_normalize_input_keeps_loose_path_for_artist_only_request():
    """곡 제목이 없는 artist-only 요청은 기존 문자열 검색을 그대로 쓴다."""
    http = FakeHttp([{"trackId": 904, "trackName": "Lilac", "artistName": "IU"}])
    lastfm = SearchableFakeLastFm([])

    result = await preprocess_input("IU", [], http, lastfm, artist_name="IU")

    assert result == ("Lilac", "IU", "itunes:904")
    assert http.requests[0][1]["term"] == "IU"


async def test_normalize_input_skips_unscored_lastfm_hits():
    """Last.fm 검색은 점수를 주지 않으므로 커버판과 무관한 곡을 직접 걸러야 한다."""
    http = EmptyHttp()
    lastfm = SearchableFakeLastFm(
        [
            FakeTrack("Karaoke Star", "봄날 (Instrumental Karaoke Version)"),
            FakeTrack("Someone Else", "전혀 다른 곡"),
            FakeTrack("BTS", "봄날"),
        ]
    )

    result = await preprocess_input(
        "Spring Day BTS",
        [],
        http,
        lastfm,
        track_title="봄날",
        artist_name="BTS",
    )

    assert result == ("봄날", "BTS", None)


async def test_normalize_input_returns_none_when_catalogs_miss():
    http = EmptyHttp()
    lastfm = SearchableFakeLastFm([])

    result = await preprocess_input("asdfqwer", [], http, lastfm)

    assert result == (None, None, None)


def _tag_tracks(tag, count):
    return [
        TrackInfo(
            name=f"{tag} Track {index}",
            artist=f"{tag} Artist {index}",
            reason_tags=[tag],
        )
        for index in range(count)
    ]


def test_weighted_round_robin_reduces_first_tag_concentration():
    groups = [
        _tag_tracks("first", 20),
        _tag_tracks("second", 20),
        _tag_tracks("third", 20),
    ]
    control = scoring._dedupe_tracks([track for group in groups for track in group])[
        :10
    ]

    treatment = scoring._weighted_round_robin(groups, 10)

    assert Counter(track.reason_tags[0] for track in control) == {"first": 10}
    assert Counter(track.reason_tags[0] for track in treatment) == {
        "first": 4,
        "second": 3,
        "third": 3,
    }


def test_weighted_round_robin_rejects_explicit_empty_weights():
    with pytest.raises(ValueError, match="weights must match track_groups"):
        scoring._weighted_round_robin(
            [_tag_tracks("first", 1)],
            1,
            weights=[],
        )


def test_weighted_round_robin_merges_duplicate_tag_reasons_and_fills():
    shared_first = TrackInfo(
        name="Shared",
        artist="Shared Artist",
        match_score=0.8,
        reason_tags=["first"],
    )
    shared_second = TrackInfo(
        name="Shared",
        artist="Shared Artist",
        match_score=0.9,
        reason_tags=["second"],
    )
    groups = [
        [shared_first, *_tag_tracks("first", 1)],
        [shared_second, *_tag_tracks("second", 2)],
    ]

    result = scoring._weighted_round_robin(groups, 4)

    assert len(result) == 4
    assert len({scoring._track_key(track) for track in result}) == 4
    assert result[0].reason_tags == ["first", "second"]
    assert result[0].match_score == 0.9


def test_weighted_round_robin_respects_artist_limit():
    groups = [
        [
            TrackInfo(name=f"Shared {index}", artist="Same Artist", reason_tags=["a"])
            for index in range(3)
        ]
        + _tag_tracks("a", 2),
        _tag_tracks("b", 3),
    ]

    result = scoring._weighted_round_robin(groups, 5, max_per_artist=1)

    assert len(result) == 5
    assert Counter(track.artist for track in result)["Same Artist"] == 1


async def test_collect_tag_tracks_dedupes_tag_queries():
    lastfm = TagFakeLastFm({"rr unique tag": [("Artist", "Track")]})

    groups = await seeds._collect_tag_tracks(
        ["rr unique tag", "RR UNIQUE TAG", "", "  "],
        lastfm,
        limit=10,
    )

    assert len(groups) == 1
    assert len(groups[0]) == 1
    assert lastfm.tag_queries == ["rr unique tag"]


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
    metadata_calls = []

    async def fake_enrich_metadata(http, tracks, *args, **kwargs):
        fields = kwargs.get("fields", "all")
        metadata_calls.append((len(tracks), tuple(fields)))
        for index, track in enumerate(tracks):
            if fields == "all" or "album_art" in fields:
                track.album_art_url = f"https://example.com/{index}.jpg"
            if fields == "all" or "source_id" in fields:
                track.bind(
                    ProviderBinding(provider="deezer", provider_track_id=str(index))
                )
            if fields == "all" or "popularity" in fields:
                track.popularity = 20 + index
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", fake_enrich_metadata
    )

    result = await tag_based_recommendations(
        EmptyHttp(),
        lastfm,
        ["korean city pop", "citypop", "japanese city pop"],
        ["emotional", "chill"],
        top_n=2,
    )

    assert result is not None
    assert set(result) == {"similar", "opposite", "hidden"}
    assert all(len(tracks) == 2 for tracks in result.values())
    assert all(track.algo == "tag_hidden" for track in result["hidden"])
    assert result["similar"][0].artist == "Stella Jang"
    assert {track.reason_tags[0] for track in result["similar"]} == {
        "korean city pop",
        "citypop",
    }
    assert {track.reason_tags[0] for track in result["opposite"]} == {
        "emotional",
        "chill",
    }
    assert all(track.album_art_url for tracks in result.values() for track in tracks)
    assert metadata_calls == [
        (15, ("popularity",)),
        (6, ("album_art", "source_id")),
    ]


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


async def test_reverse_top100_keeps_top_n_when_candidate_pool_is_thin(monkeypatch):
    """후보가 top_n보다 조금 많을 때 상위 제외 필터가 결과를 깎지 않아야 한다."""
    similar_items = [
        FakeSimilarResult(f"Artist {index}", f"Track {index}", 1.0 - (index * 0.04))
        for index in range(7)
    ]
    lastfm = SimilarFakeLastFm(similar_items)

    async def fake_enrich_metadata(http, tracks, *args, **kwargs):
        for track in tracks:
            track.popularity = 30
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", fake_enrich_metadata
    )

    reverse = await reverse_top100("Seed", "Seed Artist", EmptyHttp(), lastfm, top_n=5)

    assert len(reverse) == 5


async def test_hidden_discovery_survives_broken_similar_artist_entry(monkeypatch):
    """유사 아티스트 한 건이 깨져도 hidden 버킷 전체가 비지 않아야 한다."""

    class BrokenSimilarArtistResult:
        match = 0.99

        @property
        def item(self):
            raise ValueError("broken similar artist payload")

    similar_artists = [
        BrokenSimilarArtistResult(),
        FakeSimilarArtistResult(
            FakeSimilarArtist("Artist A", ["A Known", "A Hidden"]), 0.92
        ),
        FakeSimilarArtistResult(
            FakeSimilarArtist("Artist B", ["B Known", "B Hidden"]), 0.74
        ),
    ]
    lastfm = HiddenFakeLastFm(similar_artists)

    async def fake_enrich_metadata(http, tracks, *args, **kwargs):
        for track in tracks:
            track.popularity = 20
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", fake_enrich_metadata
    )

    result = await hidden_discovery_by_artist(
        "Seed Artist", EmptyHttp(), lastfm, top_n=2
    )

    assert result
    assert {track.artist for track in result} <= {"Artist A", "Artist B"}


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


async def test_similar_listening_pattern_uses_iu_catalog_title_alias(monkeypatch):
    lastfm = AliasSimilarFakeLastFm(
        {
            ("IU", "너랑 나 (YOU&I)"): [],
            ("IU", "You & I"): [
                FakeSimilarResult("TAEYEON", "I", 0.9),
                FakeSimilarResult("Heize", "And July", 0.8),
            ],
        }
    )

    async def fake_enrich_metadata(http, tracks, *args, **kwargs):
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", fake_enrich_metadata
    )

    result = await similar_listening_pattern(
        "너랑 나 (YOU&I)", "IU", EmptyHttp(), lastfm, top_n=2
    )

    assert [track.name for track in result] == ["I", "And July"]
    assert lastfm.track_calls[:2] == [
        ("IU", "너랑 나 (YOU&I)"),
        ("IU", "You & I"),
    ]


async def test_similar_listening_pattern_backfills_from_similar_artists(monkeypatch):
    similar_artists = [
        FakeSimilarArtistResult(
            FakeSimilarArtist("Artist A", ["Popular A", "Deep A"]),
            0.9,
        ),
        FakeSimilarArtistResult(
            FakeSimilarArtist("Artist B", ["Popular B", "Deep B"]),
            0.8,
        ),
    ]
    lastfm = CombinedFakeLastFm([], similar_artists)

    async def fake_enrich_metadata(http, tracks, *args, **kwargs):
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", fake_enrich_metadata
    )

    result = await similar_listening_pattern(
        "Unknown Track",
        "Seed Artist",
        EmptyHttp(),
        lastfm,
        top_n=2,
        prefetched=[],
    )

    assert len(result) == 2
    assert {track.artist for track in result} == {"Artist A", "Artist B"}


async def test_similar_listening_pattern_backfills_around_excluded_tracks(monkeypatch):
    similar_items = [
        FakeSimilarResult("Artist", f"Track {index}", 1.0 - (index * 0.1))
        for index in range(5)
    ]
    lastfm = SimilarFakeLastFm(similar_items)

    async def fake_enrich_metadata(http, tracks, *args, **kwargs):
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", fake_enrich_metadata
    )

    result = await similar_listening_pattern(
        "Excluded Seed",
        "Excluded Seed Artist",
        EmptyHttp(),
        lastfm,
        top_n=3,
        excluded_keys={
            scoring._track_key(TrackInfo(name="Track 0", artist="Artist")),
            scoring._track_key(TrackInfo(name="Track 1", artist="Artist")),
        },
    )

    assert [track.name for track in result] == ["Track 2", "Track 3", "Track 4"]


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


async def test_opposite_emotion_backfills_around_excluded_tracks(monkeypatch):
    lastfm = TagFakeLastFm(
        {"bright": [(f"Artist {index}", f"Track {index}") for index in range(5)]}
    )

    async def fake_seed_tags(*args, **kwargs):
        return ["calm"]

    async def fake_enrich_metadata(http, tracks, *args, **kwargs):
        return tracks

    class FakeGemini:
        def request(self, **kwargs):
            return OppositeTagAnalysis(opposite_tags=["bright"])

    monkeypatch.setattr("recommend_algo.algorithms.opposite._seed_tags", fake_seed_tags)
    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", fake_enrich_metadata
    )

    result = await opposite_emotion(
        "Seed",
        "Seed Artist",
        EmptyHttp(),
        lastfm,
        gemini_wrapper=FakeGemini(),
        top_n=3,
        excluded_keys={
            scoring._track_key(TrackInfo(name="Track 0", artist="Artist 0")),
            scoring._track_key(TrackInfo(name="Track 1", artist="Artist 1")),
        },
    )

    assert [track.name for track in result] == ["Track 2", "Track 3", "Track 4"]


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
        FakeSimilarArtistResult(
            FakeSimilarArtist("Artist C", ["C Known", "C Hidden"]), 0.62
        ),
    ]
    lastfm = HiddenFakeLastFm(similar_artists)
    popularity = {
        "A Known": 78,
        "A Hidden": 18,
        "B Known": 66,
        "B Hidden": 20,
        "C Known": 64,
        "C Hidden": 22,
    }

    async def fake_enrich_metadata(http, tracks, *args, **kwargs):
        for track in tracks:
            track.popularity = popularity.get(track.name)
        return tracks

    monkeypatch.setattr(
        "recommend_algo.common.sources.get_tracks_metadata", fake_enrich_metadata
    )

    excluded_track = TrackInfo(name="A Hidden", artist="Artist A")
    result = await hidden_discovery_by_artist(
        "Younha",
        EmptyHttp(),
        lastfm,
        top_n=2,
        excluded_keys={scoring._track_key(excluded_track)},
    )

    assert result
    assert all(track.artist != "Younha" for track in result)
    assert len(result) == 2
    assert scoring._track_key(excluded_track) not in {
        scoring._track_key(track) for track in result
    }
    assert {track.artist for track in result} <= {
        "Artist A",
        "Artist B",
        "Artist C",
    }
    assert len({track.artist for track in result}) == len(result)
