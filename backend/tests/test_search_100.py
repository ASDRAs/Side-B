"""
Natural-language search test suite: 100 cases from music-search-testcases-100.md.

  A: deterministic-mood  — _activity_mood_query fires before LLM
  B: deterministic-direct — _artist_only_query or _known_direct_query fallback
  C: llm-direct          — LLM mocked to return expected direct ParsedQuery
  D: llm-mood            — LLM mocked to return expected mood ParsedQuery
  S: scorer              — score_and_select unit tests (S1-S5)
"""
from unittest.mock import AsyncMock

import pytest

from app.pipeline.parser import parse_query
from app.pipeline.scorer import score_and_select
from app.schemas.search import CandidateTrack, LastFmLookup, ParsedQuery, Tag


def _llm_error() -> AsyncMock:
    m = AsyncMock()
    m.parse_query.side_effect = RuntimeError("LLM unavailable")
    return m


def _llm_ok(result: ParsedQuery) -> AsyncMock:
    m = AsyncMock()
    m.parse_query.return_value = result
    return m


def _track(title: str, artist: str, popularity: int, tags: list[str]) -> CandidateTrack:
    return CandidateTrack(
        providerId=f"{artist}:{title}",
        title=title,
        artist=artist,
        albumArt="",
        popularity=popularity,
        tags=[Tag(name=t) for t in tags],
    )


# ── A: deterministic mood ─────────────────────────────────────────────────────
# _activity_mood_query fires before the LLM call.

_MOOD_DET = [
    pytest.param("새벽감성 음악",               {"late-night", "chill", "acoustic"},  id="tc36"),
    pytest.param("비 오는 날 듣기 좋은 노래",    {"rainy-day",  "chill", "acoustic"},  id="tc37"),
    pytest.param("코딩할 때 집중되는 음악",       {"lo-fi", "instrumental", "focus"},   id="tc38"),
    pytest.param("공부할 때 틀어놓을 노래",       {"focus", "instrumental", "ambient"}, id="tc39"),
    pytest.param("운동할 때 신나는 음악",         {"workout", "energetic", "dance"},    id="tc40"),
    pytest.param("퇴근길에 듣기 좋은 노래",       {"commute", "chill", "pop"},          id="tc41"),
    pytest.param("드라이브할 때 듣는 노래",       {"road-trip", "indie", "feel-good"},  id="tc42"),
    pytest.param("카페에서 나올 법한 음악",       {"cafe", "acoustic", "chill"},        id="tc43"),
    pytest.param("music for programming",     {"lo-fi", "instrumental", "focus"},   id="tc55"),
    pytest.param("upbeat songs for running",  {"workout", "energetic", "dance"},    id="tc56"),
]


@pytest.mark.parametrize("raw,expected_tags", _MOOD_DET)
async def test_deterministic_mood(raw, expected_tags):
    result = await parse_query(raw, _llm_error())
    assert result.type == "mood"
    assert set(result.tags) == expected_tags


# ── B: deterministic direct ───────────────────────────────────────────────────

_ARTIST_ONLY = [
    pytest.param("Queen 노래 틀어줘", "artist:Queen", set(),              id="tc34"),
    pytest.param("아이유 노래 추천",   "artist:IU",    {"k-pop", "korean"}, id="tc35"),
]


@pytest.mark.parametrize("raw,expected_query,expected_tags", _ARTIST_ONLY)
async def test_artist_only(raw, expected_query, expected_tags):
    result = await parse_query(raw, _llm_error())
    assert result.type == "direct"
    assert result.query == expected_query
    assert set(result.tags) == expected_tags


_KNOWN_DIRECT_FALLBACK = [
    pytest.param("아이유의 너랑나",  "너랑나 IU",       id="tc1"),
    pytest.param("아이유 너랑 나",   "너랑나 IU",       id="tc2"),
    pytest.param("방탄소년단 봄날",  "Spring Day BTS", id="tc7"),
]


@pytest.mark.parametrize("raw,expected_query", _KNOWN_DIRECT_FALLBACK)
async def test_known_direct_fallback(raw, expected_query):
    result = await parse_query(raw, _llm_error())
    assert result.type == "direct"
    assert result.query == expected_query


_RAW_FALLBACK = [
    pytest.param("asdfqwer",  id="tc96"),
    pytest.param("ㅋㅋㅋㅋㅋ", id="tc97"),
]


@pytest.mark.parametrize("raw", _RAW_FALLBACK)
async def test_raw_direct_fallback(raw):
    result = await parse_query(raw, _llm_error())
    assert result.type == "direct"
    assert result.query == raw


# ── C: LLM direct ─────────────────────────────────────────────────────────────

_LLM_DIRECT = [
    pytest.param("IU You and I",                    "You and I IU",                        id="tc3"),
    pytest.param("뉴진스 hype boy",                  "Hype Boy NewJeans",                   id="tc4"),
    pytest.param("NewJeans Ditto",                  "Ditto NewJeans",                      id="tc5"),
    pytest.param("BTS Dynamite",                    "Dynamite BTS",                        id="tc6"),
    pytest.param("블랙핑크 shut down",                "Shut Down BLACKPINK",                 id="tc8"),
    pytest.param("aespa Supernova",                 "Supernova aespa",                     id="tc9"),
    pytest.param("르세라핌 antifragile",               "ANTIFRAGILE LE SSERAFIM",             id="tc10"),
    pytest.param("IVE After LIKE",                  "After LIKE IVE",                      id="tc11"),
    pytest.param("태연 만약에",                        "If Taeyeon",                          id="tc12"),
    pytest.param("악뮤 어떻게 이별까지 사랑하겠어",           "How can I love the heartbreak AKMU",  id="tc13"),
    pytest.param("임영웅 사랑은 늘 도망가",                "Love Always Run Away Lim Young Woong", id="tc14"),
    pytest.param("Queen Bohemian Rhapsody",         "Bohemian Rhapsody Queen",             id="tc15"),
    pytest.param("Bohemian Rhapsody Queen",         "Bohemian Rhapsody Queen",             id="tc16"),
    pytest.param("Beatles Let It Be",               "Let It Be The Beatles",               id="tc17"),
    pytest.param("Michael Jackson Billie Jean",     "Billie Jean Michael Jackson",         id="tc18"),
    pytest.param("Taylor Swift Anti-Hero",          "Anti-Hero Taylor Swift",              id="tc19"),
    pytest.param("Ariana Grande 7 rings",           "7 rings Ariana Grande",               id="tc20"),
    pytest.param("Olivia Rodrigo vampire",          "vampire Olivia Rodrigo",              id="tc21"),
    pytest.param("Radiohead Creep",                 "Creep Radiohead",                     id="tc22"),
    pytest.param("Nirvana Smells Like Teen Spirit", "Smells Like Teen Spirit Nirvana",     id="tc23"),
    pytest.param("Daft Punk Get Lucky",             "Get Lucky Daft Punk",                 id="tc24"),
    pytest.param("The Weeknd Blinding Lights",      "Blinding Lights The Weeknd",          id="tc25"),
    pytest.param("Ed Sheeran Shape of You",         "Shape of You Ed Sheeran",             id="tc26"),
    pytest.param("Coldplay Yellow",                 "Yellow Coldplay",                     id="tc27"),
    pytest.param("Oasis Wonderwall",                "Wonderwall Oasis",                    id="tc28"),
    pytest.param("Adele Someone Like You",          "Someone Like You Adele",              id="tc29"),
    pytest.param("Billie Eilish bad guy",           "bad guy Billie Eilish",               id="tc30"),
    pytest.param("지브리 인생의 회전목마",                  "Merry-Go-Round of Life Joe Hisaishi", id="tc31"),
    pytest.param("라라랜드 City of Stars",             "City of Stars La La Land",            id="tc32"),
    pytest.param("인터스텔라 ost",                      "Interstellar soundtrack Hans Zimmer", id="tc33"),
    pytest.param("너랑나",                            "너랑나",                              id="tc76"),
    pytest.param("Ditto",                           "Ditto",                               id="tc77"),
    pytest.param("Yellow",                          "Yellow",                              id="tc78"),
    pytest.param("봄날",                             "봄날",                               id="tc79"),
]


@pytest.mark.parametrize("raw,expected_query", _LLM_DIRECT)
async def test_llm_direct(raw, expected_query):
    pq = ParsedQuery(type="direct", query=expected_query, tags=[], lastfm_candidates=[], raw=raw)
    result = await parse_query(raw, _llm_ok(pq))
    assert result.type == "direct"
    assert result.query == expected_query


# ── D: LLM mood ───────────────────────────────────────────────────────────────

_LLM_MOOD = [
    pytest.param("잠들기 전에 듣는 잔잔한 음악",       ["sleep",            "calm",         "ambient"],      id="tc44"),
    pytest.param("아침에 기분 좋아지는 노래",          ["morning",          "happy",         "pop"],          id="tc45"),
    pytest.param("우울할 때 위로되는 노래",           ["sad",             "comfort",        "ballad"],       id="tc46"),
    pytest.param("이별하고 들을 노래",               ["breakup",         "sad",            "ballad"],       id="tc47"),
    pytest.param("설레는 봄 노래",                  ["spring",          "romantic",       "indie-pop"],    id="tc48"),
    pytest.param("여름밤에 어울리는 음악",             ["summer",          "night",          "chill"],        id="tc49"),
    pytest.param("겨울 감성 발라드",                 ["winter",          "ballad",         "k-pop"],        id="tc50"),
    pytest.param("90년대 감성 팝송",                ["90s",             "pop",            "nostalgia"],    id="tc51"),
    pytest.param("2000년대 알앤비 느낌",             ["2000s",           "rnb",            "soul"],         id="tc52"),
    pytest.param("레트로 시티팝",                    ["city-pop",        "retro",          "japanese"],     id="tc53"),
    pytest.param("lo-fi hip hop for studying",   ["lo-fi",           "hip-hop",        "study"],        id="tc54"),
    pytest.param("calm piano music",             ["calm",            "piano",          "instrumental"], id="tc57"),
    pytest.param("dark cinematic music",         ["dark",            "cinematic",      "ambient"],      id="tc58"),
    pytest.param("dreamy bedroom pop",           ["dream-pop",       "bedroom-pop",    "indie"],        id="tc59"),
    pytest.param("energetic edm festival vibe",  ["edm",             "festival",       "energetic"],    id="tc60"),
    pytest.param("sad indie folk",               ["sad",             "indie-folk",     "acoustic"],     id="tc61"),
    pytest.param("chill kpop playlist",          ["k-pop",           "chill",          "pop"],          id="tc62"),
    pytest.param("angry rock music",             ["rock",            "angry",          "alternative"],  id="tc63"),
    pytest.param("romantic jazz dinner",         ["jazz",            "romantic",       "dinner"],       id="tc64"),
    pytest.param("집중 잘 되는 피아노",               ["focus",           "piano",          "instrumental"], id="tc65"),
    pytest.param("몽환적인 신스팝",                   ["dreamy",          "synth-pop",      "electronic"],   id="tc66"),
    pytest.param("힙한 인디 음악",                   ["indie",           "alternative",    "cool"],         id="tc67"),
    pytest.param("클럽에서 나올 법한 노래",             ["club",            "dance",          "house"],        id="tc68"),
    pytest.param("혼자 산책할 때 듣는 음악",            ["walking",         "chill",          "indie"],        id="tc69"),
    pytest.param("여행 가고 싶어지는 노래",             ["travel",          "road-trip",      "pop"],          id="tc70"),
    pytest.param("마음이 편안해지는 음악",              ["relaxing",        "calm",           "ambient"],      id="tc71"),
    pytest.param("밤에 듣는 재즈",                   ["jazz",            "night",          "calm"],         id="tc72"),
    pytest.param("게임할 때 텐션 올리는 노래",           ["gaming",          "energetic",      "electronic"],   id="tc73"),
    pytest.param("집중력 올리는 백색소음 말고 음악",       ["focus",           "instrumental",   "ambient"],      id="tc74"),
    pytest.param("감성적인 어쿠스틱 기타",               ["acoustic",        "guitar",         "emotional"],    id="tc75"),
    pytest.param("아이유 같은 느낌",                  ["k-pop",           "ballad",         "soft-pop"],     id="tc80"),
    pytest.param("뉴진스 스타일 노래",                 ["k-pop",           "dance-pop",      "teen-pop"],     id="tc81"),
    pytest.param("Queen 같은 웅장한 곡",             ["classic-rock",    "anthemic",       "rock"],         id="tc82"),
    pytest.param("BTS 말고 신나는 케이팝",             ["k-pop",           "dance",          "energetic"],    id="tc83"),
    pytest.param("너무 유명하지 않은 잔잔한 노래",        ["calm",            "indie",          "acoustic"],     id="tc84"),
    pytest.param("최신 인기곡",                      ["pop",             "chart",          "hits"],         id="tc85"),
    pytest.param("2020년대 한국 발라드",              ["k-ballad",        "k-pop",          "2020s"],        id="tc86"),
    pytest.param("여자 보컬 감성곡",                   ["female-vocalists","emotional",       "ballad"],       id="tc87"),
    pytest.param("남자 아이돌 댄스곡",                 ["k-pop",           "dance",          "boy-band"],     id="tc88"),
    pytest.param("가사 없는 집중 음악",                ["instrumental",    "focus",          "ambient"],      id="tc89"),
    pytest.param("한국어 랩 신나는 거",                ["k-hip-hop",       "rap",            "energetic"],    id="tc90"),
    pytest.param("일본 애니 오프닝 느낌",               ["anime",           "j-rock",         "j-pop"],        id="tc91"),
    pytest.param("영화 예고편 같은 음악",               ["cinematic",       "epic",           "orchestral"],   id="tc92"),
    pytest.param("교회에서 부르는 찬양 느낌",            ["worship",         "gospel",         "christian"],    id="tc93"),
    pytest.param("크리스마스 분위기",                   ["christmas",       "holiday",        "winter"],       id="tc94"),
    pytest.param("할로윈 느낌 음악",                   ["halloween",       "dark",           "spooky"],       id="tc95"),
    pytest.param("노래 추천해줘",                     ["pop",             "chill"],                          id="tc98"),
    pytest.param("뭐 듣지",                         ["pop",             "chill"],                          id="tc99"),
    pytest.param("좋은 음악",                        ["pop",             "chill"],                          id="tc100"),
]


@pytest.mark.parametrize("raw,expected_tags", _LLM_MOOD)
async def test_llm_mood(raw, expected_tags):
    pq = ParsedQuery(type="mood", query=None, tags=list(expected_tags), lastfm_candidates=[], raw=raw)
    result = await parse_query(raw, _llm_ok(pq))
    assert result.type == "mood"
    assert set(result.tags) == set(expected_tags)


# ── S: scorer ─────────────────────────────────────────────────────────────────

async def test_s1_artist_prefix_selects_correct_artist():
    """artist: prefix routes to best artist similarity; IU beats NewJeans."""
    pq = ParsedQuery(
        type="direct", query="artist:IU", tags=["k-pop"],
        lastfm_candidates=[], raw="아이유 노래", confidence=1.0,
    )
    candidates = [
        _track("Ditto",     "NewJeans", 95, ["k-pop"]),
        _track("You and I", "IU",       85, ["k-pop", "dance-pop"]),
        _track("Palette",   "IU",       88, ["k-pop"]),
    ]
    idx = await score_and_select("아이유 노래", pq, candidates, AsyncMock(), use_llm=False)
    assert candidates[idx].artist == "IU"


async def test_s2_lastfm_lookup_picks_original_over_karaoke():
    """lastfm_candidates lookup score confirms Queen original over karaoke cover."""
    pq = ParsedQuery(
        type="direct",
        query="Bohemian Rhapsody Queen",
        tags=[],
        lastfm_candidates=[LastFmLookup(artist="Queen", title="Bohemian Rhapsody")],
        raw="Bohemian Rhapsody Queen",
        confidence=0.0,
    )
    candidates = [
        _track("Bohemian Rhapsody (Karaoke)", "Various Artists", 70, []),
        _track("Bohemian Rhapsody",           "Queen",           95, ["classic-rock"]),
        _track("Somebody to Love",            "Queen",           88, ["classic-rock"]),
    ]
    idx = await score_and_select("Bohemian Rhapsody Queen", pq, candidates, AsyncMock(), use_llm=False)
    assert candidates[idx].title == "Bohemian Rhapsody"
    assert candidates[idx].artist == "Queen"


async def test_s3_tag_overlap_beats_popularity_for_mood():
    """Mood: matching-tag low-popularity track is preferred over untagged chart hit."""
    pq = ParsedQuery(
        type="mood", query=None,
        tags=["late-night", "chill", "acoustic"],
        raw="새벽감성 음악", confidence=1.0,
    )
    candidates = [
        _track("Popular Song", "Big Artist",   99, ["pop"]),
        _track("Night Vibes",  "Small Artist", 40, ["late-night", "chill", "acoustic"]),
    ]
    idx = await score_and_select("새벽감성 음악", pq, candidates, AsyncMock(), use_llm=False)
    assert idx == 1


async def test_s4_workout_tags_beat_sad_ballad_popularity():
    """Workout mood: workout/energetic-tagged track beats high-popularity ballad."""
    pq = ParsedQuery(
        type="mood", query=None,
        tags=["workout", "energetic", "dance"],
        raw="운동할 때 신나는 음악", confidence=1.0,
    )
    candidates = [
        _track("Sad Song", "Ballad Artist", 98, ["sad", "ballad"]),
        _track("Power Up",  "EDM Act",       50, ["workout", "energetic", "dance"]),
    ]
    idx = await score_and_select("운동할 때 신나는 음악", pq, candidates, AsyncMock(), use_llm=False)
    assert idx == 1


async def test_s5_popularity_is_tiebreaker_when_tag_overlap_equal():
    """When all candidates share the same tag overlap count, highest popularity wins."""
    pq = ParsedQuery(
        type="mood", query=None,
        tags=["calm", "indie", "acoustic"],
        raw="너무 유명하지 않은 잔잔한 노래", confidence=1.0,
    )
    candidates = [
        _track("Calm Pop",   "Famous Artist",  90, ["calm"]),
        _track("Calm Indie", "Unknown Artist",  30, ["calm"]),
    ]
    idx = await score_and_select("너무 유명하지 않은 잔잔한 노래", pq, candidates, AsyncMock(), use_llm=False)
    assert idx == 0
