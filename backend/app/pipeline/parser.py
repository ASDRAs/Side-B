import asyncio
import re
import time

from app.config.rules import ACTIVITY_MOOD_RULES
from app.pipeline.errors import ServiceUnavailableError
from app.schemas.search import LastFmLookup, ParsedQuery
from app.services.llm import GeminiClient


LLM_PARSE_TIMEOUT_SECONDS = 4.0

_PARSE_CACHE: dict[str, tuple[float, ParsedQuery]] = {}
_PARSE_CACHE_TTL = 300.0


async def parse_query(raw: str, llm: GeminiClient, use_deterministic: bool = True) -> ParsedQuery:
    _cache_key = f"{raw}:{use_deterministic}"
    _entry = _PARSE_CACHE.get(_cache_key)
    if _entry and time.monotonic() - _entry[0] < _PARSE_CACHE_TTL:
        return _entry[1]

    result = await _parse_query_uncached(raw, llm, use_deterministic)
    _PARSE_CACHE[_cache_key] = (time.monotonic(), result)
    return result


async def _parse_query_uncached(raw: str, llm: GeminiClient, use_deterministic: bool = True) -> ParsedQuery:
    if use_deterministic:
        mood = _activity_mood_query(raw)
        if mood:
            return mood
        artist_only = _artist_only_query(raw)
        if artist_only:
            return artist_only

    try:
        parsed = await asyncio.wait_for(llm.parse_query(raw), timeout=LLM_PARSE_TIMEOUT_SECONDS)
    except ServiceUnavailableError:
        raise
    except Exception:
        if use_deterministic:
            fallback = _known_direct_query(raw)
            if fallback:
                return fallback
        return ParsedQuery(type="direct", query=raw, tags=[], lastfm_candidates=[], raw=raw)

    if use_deterministic and parsed.type == "direct" and _looks_like_activity_query(raw):
        mood = _activity_mood_query(raw)
        if mood:
            return mood

    if parsed.type == "direct" and not parsed.query:
        return ParsedQuery(
            type="direct",
            query=raw,
            tags=parsed.tags,
            lastfm_candidates=parsed.lastfm_candidates,
            raw=raw,
        )
    if parsed.type == "mood" and not parsed.tags:
        return ParsedQuery(type="direct", query=raw, tags=[], lastfm_candidates=[], raw=raw)
    if len(parsed.lastfm_candidates) >= 2:
        return parsed.model_copy(update={"confidence": 0.8})
    return parsed


def _activity_mood_query(raw: str) -> ParsedQuery | None:
    lowered = raw.lower()
    compact = re.sub(r"\s+", "", lowered)
    if _known_direct_query(raw):
        return None

    if re.search(r"\b(music|songs?)\s+for\s+(programming|coding|work|concentration|focus)\b", lowered):
        return ParsedQuery(type="mood", query=None, tags=["lo-fi", "instrumental", "focus"], raw=raw, confidence=1.0)
    if re.search(r"\b(music|songs?)\s+for\s+(study|studying)\b", lowered):
        return ParsedQuery(type="mood", query=None, tags=["focus", "instrumental", "ambient"], raw=raw, confidence=1.0)
    if re.search(r"\b((music|songs?)\s+for\s+)?(workout|working out|running|gym)(\s+(music|playlist|songs?))?\b", lowered):
        return ParsedQuery(type="mood", query=None, tags=["workout", "energetic", "dance"], raw=raw, confidence=1.0)
    if re.search(r"\b(music|songs?)\s+for\s+(driving|road trip|commute)\b", lowered):
        return ParsedQuery(type="mood", query=None, tags=["road-trip", "indie", "feel-good"], raw=raw, confidence=1.0)
    if re.search(r"\b(relaxing|relax|chill)\s+(music|playlist|songs?)\b", lowered):
        return ParsedQuery(type="mood", query=None, tags=["chill", "ambient", "relaxing"], raw=raw, confidence=1.0)
    if compact in {"edm", "electronic", "house", "techno"}:
        return ParsedQuery(type="mood", query=None, tags=["edm", "electronic", "dance"], raw=raw, confidence=1.0)

    for keywords, tags in ACTIVITY_MOOD_RULES:
        if any(word in raw for word in keywords):
            return ParsedQuery(type="mood", query=None, tags=tags, raw=raw, confidence=1.0)

    return None


def _artist_only_query(raw: str) -> ParsedQuery | None:
    cleaned = re.sub(
        r"(노래|음악|곡|추천|틀어줘|들려줘|듣고\s*싶어|찾아줘|좀|please)",
        " ",
        raw,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    aliases = {
        "queen": "Queen",
        "iu": "IU",
        "아이유": "IU",
        "bts": "BTS",
        "방탄": "BTS",
        "방탄소년단": "BTS",
        "뉴진스": "NewJeans",
        "newjeans": "NewJeans",
        "new jeans": "NewJeans",
        "블랙핑크": "BLACKPINK",
        "blackpink": "BLACKPINK",
        "프로미스나인": "fromis_9",
        "fromis_9": "fromis_9",
        "fromis 9": "fromis_9",
    }
    artist = aliases.get(cleaned.lower())
    if not artist:
        return None
    tags = ["k-pop", "korean"] if artist in {"IU", "BTS", "NewJeans", "BLACKPINK", "fromis_9"} else []
    return ParsedQuery(type="direct", query=f"artist:{artist}", tags=tags, lastfm_candidates=[], raw=raw, confidence=1.0)


def _known_direct_query(raw: str) -> ParsedQuery | None:
    lowered = raw.lower()
    compact = re.sub(r"\s+", "", lowered)

    if ("아이유" in raw or "iu" in lowered) and ("너랑나" in compact or "너랑 나" in raw):
        return ParsedQuery(
            type="direct",
            query="너랑나 IU",
            tags=["k-pop", "dance-pop", "korean"],
            lastfm_candidates=[
                LastFmLookup(artist="IU", title="You&I"),
                LastFmLookup(artist="IU", title="You & I"),
            ],
            raw=raw,
            confidence=1.0,
        )

    if ("방탄" in raw or "bts" in lowered) and "봄날" in raw:
        return ParsedQuery(
            type="direct",
            query="Spring Day BTS",
            tags=["k-pop", "ballad"],
            lastfm_candidates=[LastFmLookup(artist="BTS", title="Spring Day")],
            raw=raw,
            confidence=1.0,
        )

    if ("아이유" in raw or "iu" in lowered) and "밤편지" in compact:
        return ParsedQuery(
            type="direct",
            query="Through the Night IU",
            tags=["k-pop", "ballad", "korean"],
            lastfm_candidates=[LastFmLookup(artist="IU", title="Through the Night")],
            raw=raw,
            confidence=1.0,
        )

    return None


def _looks_like_activity_query(raw: str) -> bool:
    lowered = raw.lower()
    return bool(
        re.search(r"\b(music|songs?)\s+for\s+", lowered)
        or any(word in raw for word in ["프로그래밍", "코딩", "공부", "운동", "드라이브", "운전", "새벽", "감성", "카페"])
    )
