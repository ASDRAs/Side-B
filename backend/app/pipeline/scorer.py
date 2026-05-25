import asyncio
import re

from app.pipeline.errors import ServiceUnavailableError
from app.schemas.search import CandidateTrack, ParsedQuery
from app.services.llm import GeminiClient
from app.utils.text import normalize_tag, normalize_text, sim

LLM_SCORE_TIMEOUT_SECONDS = 2.0


async def score_and_select(
    raw_query: str,
    parsed_query: ParsedQuery,
    candidates: list[CandidateTrack],
    llm: GeminiClient,
    use_llm: bool = True,
) -> int:
    deterministic_index = _deterministic_rerank(parsed_query, candidates)
    if deterministic_index is not None:
        return deterministic_index

    if not use_llm or parsed_query.type == "mood" or parsed_query.confidence >= 0.75:
        overlap_index = _tag_overlap_index(parsed_query, candidates)
        return (
            overlap_index
            if overlap_index is not None
            else _highest_popularity_index(candidates)
        )

    try:
        index = await asyncio.wait_for(
            llm.score_candidates(raw_query, candidates[:5]),
            timeout=LLM_SCORE_TIMEOUT_SECONDS,
        )
    except ServiceUnavailableError:
        raise
    except Exception:
        overlap_index = _tag_overlap_index(parsed_query, candidates)
        return (
            overlap_index
            if overlap_index is not None
            else _highest_popularity_index(candidates)
        )

    if 0 <= index < len(candidates):
        return index
    overlap_index = _tag_overlap_index(parsed_query, candidates)
    return (
        overlap_index
        if overlap_index is not None
        else _highest_popularity_index(candidates)
    )


def _highest_popularity_index(candidates: list[CandidateTrack]) -> int:
    return max(range(len(candidates)), key=lambda idx: candidates[idx].popularity)


def _deterministic_rerank(
    parsed_query: ParsedQuery, candidates: list[CandidateTrack]
) -> int | None:
    if parsed_query.type != "direct" or not candidates:
        return None

    artist_query = _artist_field_query(parsed_query.query or "")
    if artist_query:
        return _best_artist_index(artist_query, candidates)

    ref_tokens = _query_tokens(parsed_query.query or parsed_query.raw)
    if not ref_tokens and not parsed_query.lastfm_candidates:
        return None

    best_score = 0.0
    best_index: int | None = None
    for index, candidate in enumerate(candidates[:5]):
        title_score = _best_token_similarity(ref_tokens, candidate.title)
        artist_score = _best_token_similarity(ref_tokens, candidate.artist)
        lookup_score = _lastfm_lookup_score(parsed_query, candidate)

        artist_only = _is_artist_only(ref_tokens, candidate)
        confirmed = (
            _title_confirmed(title_score, artist_score)
            or lookup_score >= 0.75
            or (artist_only and artist_score >= 0.85)
        )
        if not confirmed:
            continue

        combined = title_score * 0.5 + artist_score * 0.3 + lookup_score * 0.2
        if combined > best_score:
            best_score = combined
            best_index = index

    return best_index


def _query_tokens(query: str) -> list[str]:
    normalized = normalize_text(query)
    return [token for token in normalized.split() if token]


def _artist_field_query(query: str) -> str | None:
    match = re.search(r"\bartist\s*:\s*([^\s].*)", query, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def _best_artist_index(artist: str, candidates: list[CandidateTrack]) -> int | None:
    best_score = 0.0
    best_index: int | None = None
    for index, candidate in enumerate(candidates[:5]):
        score = sim(artist, candidate.artist)
        if score > best_score:
            best_score = score
            best_index = index
    return best_index if best_score >= 0.80 else None


def _best_token_similarity(tokens: list[str], value: str) -> float:
    return max((sim(token, value) for token in tokens), default=0.0)


def _lastfm_lookup_score(parsed_query: ParsedQuery, candidate: CandidateTrack) -> float:
    best_score = 0.0
    for lookup in parsed_query.lastfm_candidates:
        title_score = sim(lookup.title, candidate.title)
        artist_score = sim(lookup.artist, candidate.artist)
        best_score = max(best_score, title_score * 0.7 + artist_score * 0.3)
    return best_score


def _title_confirmed(title_score: float, artist_score: float) -> bool:
    if title_score >= 0.88:
        return True
    return title_score >= 0.80 and artist_score >= 0.50


def _is_artist_only(tokens: list[str], candidate: CandidateTrack) -> bool:
    if not tokens:
        return False
    best_title = _best_token_similarity(tokens, candidate.title)
    query_text = " ".join(tokens)
    return sim(query_text, candidate.artist) >= 0.85 and best_title < 0.40


def _tag_overlap_index(
    parsed_query: ParsedQuery, candidates: list[CandidateTrack]
) -> int | None:
    query_tags = {normalize_tag(tag) for tag in parsed_query.tags}
    if not query_tags:
        return None

    best_score = 0
    best_index: int | None = None
    for index, candidate in enumerate(candidates):
        candidate_tags = {normalize_tag(tag.name) for tag in candidate.tags}
        score = len(query_tags & candidate_tags)
        if score > best_score:
            best_score = score
            best_index = index
    return best_index
