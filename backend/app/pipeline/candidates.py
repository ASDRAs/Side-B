import asyncio
import re

from app.config.rules import SIMPLE_TAG_RULES
from app.schemas.search import CandidateTrack, LastFmLookup, ParsedQuery, Tag
from app.services.catalog import CatalogClient
from app.services.lastfm import LastFmClient
from app.utils.text import normalize_tag

KO_MUSIC_SIGNALS = {
    "아이유",
    "iu",
    "bts",
    "방탄",
    "케이팝",
    "k-pop",
    "kpop",
    "한국 음악",
    "한국노래",
    "korean music",
    "blackpink",
    "뉴진스",
    "aespa",
    "stray kids",
    "exo",
    "twice",
}

JA_MUSIC_SIGNALS = {
    "j-pop",
    "jpop",
    "일본 노래",
    "일본 음악",
    "japanese music",
    "요아소비",
    "yoasobi",
    "あいみょん",
    "米津玄師",
    "지브리",
    "ghibli",
    "애니",
    "anime",
    "시티팝",
    "city pop",
    "히사이시",
    "joe hisaishi",
    "하울",
    "토토로",
}


async def collect_candidates(
    parsed: ParsedQuery,
    catalog: CatalogClient,
    lastfm: LastFmClient,
) -> list[CandidateTrack]:
    if parsed.type == "mood":
        candidates = await _collect_mood_candidates(parsed, catalog, lastfm)
        if candidates:
            return candidates
        return await _catalog_candidates(
            catalog,
            " ".join(parsed.tags) or parsed.raw,
            fallback_tags=parsed.tags,
            raw_query=parsed.raw,
            parsed_type=parsed.type,
        )

    query = parsed.query or parsed.raw
    candidates = await _catalog_candidates(
        catalog,
        query,
        lastfm_candidates=parsed.lastfm_candidates,
        fallback_tags=parsed.tags,
        raw_query=f"{parsed.raw} {query}",
        parsed_type=parsed.type,
    )
    if candidates:
        return candidates

    cleaned = _clean_query(query)
    if cleaned != query:
        candidates = await _catalog_candidates(
            catalog,
            cleaned,
            lastfm_candidates=parsed.lastfm_candidates,
            fallback_tags=parsed.tags,
            raw_query=f"{parsed.raw} {cleaned}",
            parsed_type=parsed.type,
        )
        if candidates:
            return candidates

    fallback_tags = _simple_tags(parsed.raw)
    if not _should_try_mood_fallback(parsed.raw, fallback_tags):
        return []
    mood = ParsedQuery(
        type="mood",
        query=None,
        tags=fallback_tags,
        lastfm_candidates=[],
        raw=parsed.raw,
    )
    return await _collect_mood_candidates(mood, catalog, lastfm)


async def _collect_mood_candidates(
    parsed: ParsedQuery,
    catalog: CatalogClient,
    lastfm: LastFmClient,
) -> list[CandidateTrack]:
    tags = parsed.tags[:3]
    if not tags:
        return []

    tag_track_lists = await asyncio.gather(
        *[lastfm.tag_top_tracks(tag, limit=5) for tag in tags],
        return_exceptions=True,
    )

    all_candidates: list[CandidateTrack] = []
    for tag, top_tracks in zip(tags, tag_track_lists, strict=False):
        if isinstance(top_tracks, Exception) or not top_tracks:
            continue
        top_tracks_to_map = top_tracks[:5]
        results = await asyncio.gather(
            *[
                catalog.search_track_by_artist_title(item["artist"], item["title"])
                for item in top_tracks_to_map
            ],
            return_exceptions=True,
        )
        for item, result in zip(top_tracks_to_map, results, strict=False):
            if isinstance(result, Exception) or result is None:
                continue
            candidate = catalog.normalize_track(result)
            if candidate:
                all_candidates.append(
                    _with_lookup_context(
                        candidate,
                        lastfm_candidates=[
                            LastFmLookup(artist=item["artist"], title=item["title"])
                        ],
                        fallback_tags=parsed.tags,
                        raw_query=parsed.raw,
                        parsed_type=parsed.type,
                    )
                )

    return _dedupe(all_candidates)[:5] if all_candidates else []


async def _catalog_candidates(
    catalog: CatalogClient,
    query: str,
    lastfm_candidates: list[LastFmLookup] | None = None,
    fallback_tags: list[str] | None = None,
    raw_query: str = "",
    parsed_type: str = "direct",
) -> list[CandidateTrack]:
    tracks = await catalog.search_tracks(query, limit=5)
    candidates = [catalog.normalize_track(track) for track in tracks]
    return _dedupe(
        [
            _with_lookup_context(
                candidate,
                lastfm_candidates=lastfm_candidates or [],
                fallback_tags=fallback_tags or [],
                raw_query=raw_query or query,
                parsed_type=parsed_type,
            )
            for candidate in candidates
            if candidate is not None
        ]
    )[:5]


def _dedupe(candidates: list[CandidateTrack]) -> list[CandidateTrack]:
    seen: set[str] = set()
    deduped: list[CandidateTrack] = []
    for candidate in candidates:
        if candidate.providerId in seen:
            continue
        seen.add(candidate.providerId)
        deduped.append(candidate)
    return deduped


def _with_lookup_context(
    candidate: CandidateTrack,
    lastfm_candidates: list[LastFmLookup],
    fallback_tags: list[str],
    raw_query: str,
    parsed_type: str,
) -> CandidateTrack:
    return candidate.model_copy(
        update={
            "lastfm_candidates": lastfm_candidates,
            "fallback_tags": [
                Tag(name=tag, count=0)
                for tag in _with_language_tags(
                    fallback_tags,
                    raw=raw_query,
                    parsed_type=parsed_type,
                    candidate_artist=candidate.artist,
                )
            ],
        }
    )


def _with_language_tags(
    tags: list[str],
    raw: str,
    parsed_type: str,
    candidate_artist: str = "",
) -> list[str]:
    normalized = [normalize_tag(tag) for tag in tags if normalize_tag(tag)]
    for language_tag in _infer_language_tags(
        raw=raw,
        parsed_type=parsed_type,
        tags=normalized,
        candidate_artist=candidate_artist,
    ):
        if language_tag not in normalized:
            normalized.append(language_tag)
    return normalized[:5]


def _infer_language_tags(
    raw: str,
    parsed_type: str,
    tags: list[str],
    candidate_artist: str = "",
) -> list[str]:
    lowered = raw.lower()
    normalized_tags = {normalize_tag(tag) for tag in tags}
    inferred: list[str] = []

    has_ko_signal = any(signal in lowered for signal in KO_MUSIC_SIGNALS) or bool(
        normalized_tags & {"k-pop", "k-ballad", "k-hip-hop", "korean"}
    )
    has_ja_signal = any(signal in lowered for signal in JA_MUSIC_SIGNALS) or bool(
        normalized_tags & {"j-pop", "j-rock", "anime", "city-pop", "japanese"}
    )

    if parsed_type == "direct":
        artist_lower = candidate_artist.lower()
        has_ko_signal = has_ko_signal or bool(re.search(r"[가-힣]", artist_lower))
        has_ja_signal = has_ja_signal or bool(re.search(r"[぀-ヿ一-鿿]", artist_lower))

    if has_ko_signal:
        inferred.append("korean")

    if has_ja_signal:
        inferred.append("japanese")

    return inferred


def _clean_query(query: str) -> str:
    cleaned = re.sub(r"[\"'`]", " ", query)
    cleaned = re.sub(r"\b(of|the|a|an)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"(이라는|라는|의|노래|음악|곡|추천|틀어줘|들려줘|듣고\s*싶어|찾아줘)",
        " ",
        cleaned,
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def _should_try_mood_fallback(raw: str, fallback_tags: list[str]) -> bool:
    lowered = raw.lower()
    if re.fullmatch(r"[a-z]{6,}", lowered) and fallback_tags == [lowered]:
        return False
    if re.fullmatch(r"[ㅋㅎㅠㅜ]+", raw):
        return False

    fallback_set = set(fallback_tags)
    known_mood_tags = {
        "focus",
        "instrumental",
        "lo-fi",
        "workout",
        "energetic",
        "dance",
        "road-trip",
        "indie",
        "feel-good",
        "sleep",
        "ambient",
        "calm",
        "late-night",
        "chill",
        "acoustic",
        "sad",
        "ballad",
        "emotional",
        "happy",
        "upbeat",
        "pop",
    }
    return bool(fallback_set & known_mood_tags)


def _simple_tags(raw: str) -> list[str]:
    lowered = raw.lower()
    for keywords, tags in SIMPLE_TAG_RULES:
        if any(word in lowered for word in keywords):
            return tags
    return [token for token in re.split(r"\W+", lowered) if token][:3] or ["chill"]
