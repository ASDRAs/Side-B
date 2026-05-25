from app.pipeline.parser import parse_query
from app.schemas.search import ParsedQuery


class BrokenLlm:
    async def parse_query(self, raw):
        raise ValueError("bad json")


class RecordingLlm:
    def __init__(self):
        self.calls = []

    async def parse_query(self, raw):
        self.calls.append(raw)
        return ParsedQuery(
            type="direct", query="Gemini IU You and I", tags=["k-pop"], raw=raw
        )


async def test_parser_lets_llm_handle_known_direct_query_when_available():
    llm = RecordingLlm()

    parsed = await parse_query("아이유 너랑나", llm)

    assert llm.calls == ["아이유 너랑나"]
    assert parsed.type == "direct"
    assert parsed.query == "Gemini IU You and I"


async def test_parser_falls_back_to_direct_query_on_bad_llm_json():
    parsed = await parse_query("아이유 너랑나", BrokenLlm())

    assert parsed.type == "direct"
    assert parsed.query == "너랑나 IU"
    assert [lookup.title for lookup in parsed.lastfm_candidates] == ["You&I", "You & I"]


async def test_parser_forces_music_for_activity_to_mood():
    parsed = await parse_query("music for programming", BrokenLlm())

    assert parsed.type == "mood"
    assert parsed.query is None
    assert parsed.tags == ["lo-fi", "instrumental", "focus"]


async def test_parser_forces_common_mood_queries_without_llm():
    parsed = await parse_query("workout playlist", BrokenLlm())

    assert parsed.type == "mood"
    assert parsed.tags == ["workout", "energetic", "dance"]


async def test_parser_handles_artist_only_korean_filler_without_llm():
    parsed = await parse_query("Queen 노래 틀어줘", BrokenLlm())

    assert parsed.type == "direct"
    assert parsed.query == "artist:Queen"


async def test_parser_falls_back_to_direct_query_on_bad_llm_json_for_unknown_query():
    parsed = await parse_query("unknown title", BrokenLlm())

    assert parsed.type == "direct"
    assert parsed.query == "unknown title"
    assert parsed.tags == []
