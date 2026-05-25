import pytest

from app.pipeline.candidates import collect_candidates
from app.pipeline.errors import NoResultsError
from app.pipeline.orchestrator import enrich_all, search_pipeline
from app.schemas.search import CandidateTrack, LastFmLookup, ParsedQuery, Tag, Track

CATALOG_TRACK = {
    "id": "catalog-track-1",
    "name": "You and I",
    "artists": [{"name": "IU"}],
    "album": {"images": [{"url": "https://example.com/cover.jpg"}]},
    "popularity": 72,
}

BAD_CATALOG_TRACK = {
    "id": "bad-track-1",
    "name": "You and I (Park Bom)",
    "artists": [{"name": "2NE1"}],
    "album": {"images": [{"url": "https://example.com/bad-cover.jpg"}]},
    "popularity": 90,
}

IU_CATALOG_TRACK = {
    "id": "iu-track-1",
    "name": "You & I",
    "artists": [{"name": "IU"}],
    "album": {"images": [{"url": "https://example.com/iu-cover.jpg"}]},
    "popularity": 72,
}


class FakeCatalog:
    def __init__(self):
        self.queries = []

    async def search_tracks(self, query, limit=5):
        self.queries.append(query)
        return [CATALOG_TRACK]

    async def search_track_by_artist_title(self, artist, title):
        return CATALOG_TRACK

    @staticmethod
    def normalize_track(track):
        return CandidateTrack(
            providerId=track["id"],
            artist=track["artists"][0]["name"],
            title=track["name"],
            albumArt=track["album"]["images"][0]["url"],
            popularity=track["popularity"],
            tags=[],
        )


class FakeLastFm:
    def __init__(self):
        self.lookups = []

    async def tag_top_tracks(self, tag, limit=5):
        return [{"artist": "IU", "title": "You and I"}]

    async def track_top_tags(self, artist, title):
        self.lookups.append((artist, title))
        return [Tag(name="k-pop", count=100)]


class FailingTagsLastFm(FakeLastFm):
    async def track_top_tags(self, artist, title):
        raise RuntimeError("miss")


class KoreanMissLastFm(FakeLastFm):
    async def track_top_tags(self, artist, title):
        self.lookups.append((artist, title))
        if (artist, title) == ("BTS", "Spring Day"):
            return [Tag(name="k-pop", count=100), Tag(name="ballad", count=80)]
        return []


class FakeLlm:
    def __init__(self, parsed):
        self.parsed = parsed

    async def parse_query(self, raw):
        return self.parsed

    async def score_candidates(self, raw_query, candidates):
        return 0


class ForbiddenLlm:
    async def parse_query(self, raw):
        raise AssertionError("parse_query should not be called")

    async def score_candidates(self, raw_query, candidates):
        raise AssertionError("score_candidates should not be called")


class EmptyCatalog(FakeCatalog):
    async def search_tracks(self, query, limit=5):
        return []

    async def search_track_by_artist_title(self, artist, title):
        return None


class EmptyLastFm(FakeLastFm):
    async def tag_top_tracks(self, tag, limit=5):
        return []


class MultiResultCatalog(FakeCatalog):
    async def search_tracks(self, query, limit=5):
        self.queries.append(query)
        return [BAD_CATALOG_TRACK, IU_CATALOG_TRACK]


class RecordingLastFm(FakeLastFm):
    def __init__(self):
        super().__init__()
        self.tag_limits = []

    async def tag_top_tracks(self, tag, limit=5):
        self.tag_limits.append(limit)
        return [{"artist": "IU", "title": f"You and I {index}"} for index in range(10)]


async def test_search_pipeline_returns_track_for_direct_query():
    result = await search_pipeline(
        "아이유의 너랑나",
        catalog=FakeCatalog(),
        lastfm=FakeLastFm(),
        llm=FakeLlm(
            ParsedQuery(
                type="direct", query="너랑나 IU", tags=[], raw="아이유의 너랑나"
            )
        ),
    )

    Track.model_validate(result.model_dump())
    assert result.providerId == "catalog-track-1"
    assert "k-pop" in [tag.name for tag in result.tags]


async def test_search_pipeline_can_skip_llm_and_search_catalog_directly():
    catalog = FakeCatalog()

    result = await search_pipeline(
        "프로미스나인 DM",
        catalog=catalog,
        lastfm=FakeLastFm(),
        llm=ForbiddenLlm(),
        use_llm=False,
    )

    assert result.providerId == "catalog-track-1"
    assert catalog.queries == ["프로미스나인 DM"]


async def test_direct_query_reranks_artist_title_match_before_llm_choice():
    result = await search_pipeline(
        "아이유의 너랑나",
        catalog=MultiResultCatalog(),
        lastfm=KoreanMissLastFm(),
        llm=FakeLlm(
            ParsedQuery(
                type="direct",
                query="너랑나 IU",
                tags=["k-pop", "dance-pop"],
                lastfm_candidates=[
                    LastFmLookup(artist="IU", title="You&I"),
                    LastFmLookup(artist="IU", title="You & I"),
                ],
                raw="아이유의 너랑나",
            )
        ),
    )

    assert result.providerId == "iu-track-1"
    assert result.artist == "IU"


async def test_search_pipeline_uses_lastfm_tag_path_for_mood_query():
    catalog = FakeCatalog()
    result = await search_pipeline(
        "새벽감성 음악",
        catalog=catalog,
        lastfm=FakeLastFm(),
        llm=FakeLlm(
            ParsedQuery(type="mood", query=None, tags=["chill"], raw="새벽감성 음악")
        ),
    )

    assert result.artist == "IU"
    assert catalog.queries == []


async def test_korean_mood_query_does_not_add_korean_fallback_tag_by_script_only():
    candidates = await collect_candidates(
        ParsedQuery(
            type="mood",
            query=None,
            tags=["late-night", "chill"],
            raw="새벽감성 음악",
        ),
        catalog=FakeCatalog(),
        lastfm=FakeLastFm(),
    )

    assert "korean" not in [tag.name for tag in candidates[0].fallback_tags]


async def test_mood_tag_top_tracks_requests_five_candidates_before_catalog_mapping():
    lastfm = RecordingLastFm()
    await collect_candidates(
        ParsedQuery(
            type="mood",
            query=None,
            tags=["focus"],
            raw="music for programming",
        ),
        catalog=FakeCatalog(),
        lastfm=lastfm,
    )

    assert lastfm.tag_limits == [5]


async def test_search_pipeline_raises_no_results_when_all_fallbacks_fail():
    with pytest.raises(NoResultsError):
        await search_pipeline(
            "asdfqwer",
            catalog=EmptyCatalog(),
            lastfm=EmptyLastFm(),
            llm=FakeLlm(
                ParsedQuery(type="direct", query="asdfqwer", tags=[], raw="asdfqwer")
            ),
        )


async def test_lastfm_tag_miss_keeps_empty_tags():
    candidate = CandidateTrack(
        providerId="id",
        artist="Artist",
        title="Title",
        albumArt="https://example.com/cover.jpg",
        popularity=50,
    )

    [result] = await enrich_all([candidate], FailingTagsLastFm())

    assert result.tags == []


async def test_enrich_uses_lastfm_lookup_candidates_before_korean_title():
    candidate = CandidateTrack(
        providerId="id",
        artist="BTS",
        title="봄날",
        albumArt="https://example.com/cover.jpg",
        popularity=80,
        lastfm_candidates=[LastFmLookup(artist="BTS", title="Spring Day")],
    )
    lastfm = KoreanMissLastFm()

    [result] = await enrich_all([candidate], lastfm)

    assert result.tags[0].name == "k-pop"
    assert lastfm.lookups == [("BTS", "Spring Day")]


async def test_enrich_uses_parser_fallback_tags_when_lastfm_has_no_tags():
    candidate = CandidateTrack(
        providerId="id",
        artist="IU",
        title="너랑나",
        albumArt="https://example.com/cover.jpg",
        popularity=72,
        lastfm_candidates=[LastFmLookup(artist="IU", title="You&I")],
        fallback_tags=[Tag(name="k-pop", count=0), Tag(name="dance-pop", count=0)],
    )
    lastfm = KoreanMissLastFm()

    [result] = await enrich_all([candidate], lastfm)

    assert [tag.name for tag in result.tags] == ["k-pop", "dance-pop"]
    assert lastfm.lookups == [("IU", "You&I"), ("IU", "너랑나")]


async def test_direct_korean_context_adds_korean_fallback_tag():
    result = await search_pipeline(
        "아이유의 너랑나",
        catalog=FakeCatalog(),
        lastfm=KoreanMissLastFm(),
        llm=FakeLlm(
            ParsedQuery(
                type="direct",
                query="너랑나 IU",
                tags=["k-pop", "dance-pop"],
                lastfm_candidates=[LastFmLookup(artist="IU", title="You&I")],
                raw="아이유의 너랑나",
            )
        ),
    )

    assert [tag.name for tag in result.tags] == ["k-pop", "dance-pop", "korean"]


async def test_japanese_context_adds_japanese_fallback_tag():
    candidates = await collect_candidates(
        ParsedQuery(
            type="direct",
            query="Merry-Go-Round of Life Joe Hisaishi",
            tags=["soundtrack"],
            lastfm_candidates=[
                LastFmLookup(artist="Joe Hisaishi", title="Merry-Go-Round of Life")
            ],
            raw="지브리 인생의 회전목마",
        ),
        catalog=FakeCatalog(),
        lastfm=KoreanMissLastFm(),
    )

    assert "japanese" in [tag.name for tag in candidates[0].fallback_tags]
