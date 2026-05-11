import asyncio

from app.pipeline.candidates import collect_candidates
from app.pipeline.errors import NoResultsError, SearchTimeoutError, ServiceUnavailableError
from app.pipeline.parser import parse_query
from app.pipeline.scorer import score_and_select
from app.schemas.search import CandidateTrack, LastFmLookup, ParsedQuery, Tag, Track
from app.services.lastfm import LastFmClient
from app.services.llm import GeminiClient
from app.services.catalog import CatalogClient

LASTFM_TAG_TIMEOUT_SECONDS = 1.5


async def search_pipeline(
    query: str,
    catalog: CatalogClient,
    lastfm: LastFmClient,
    llm: GeminiClient,
    timeout_seconds: float = 8.0,
    use_deterministic: bool = True,
    use_llm: bool = True,
) -> Track:
    try:
        return await asyncio.wait_for(
            _search_pipeline(
                query=query,
                catalog=catalog,
                lastfm=lastfm,
                llm=llm,
                use_deterministic=use_deterministic,
                use_llm=use_llm,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise SearchTimeoutError(query) from exc


async def _search_pipeline(
    query: str,
    catalog: CatalogClient,
    lastfm: LastFmClient,
    llm: GeminiClient,
    use_deterministic: bool,
    use_llm: bool,
) -> Track:
    if use_llm:
        parsed = await parse_query(query, llm, use_deterministic=use_deterministic)
    else:
        parsed = ParsedQuery(type="direct", query=query, tags=[], lastfm_candidates=[], raw=query)

    candidates = await collect_candidates(parsed, catalog, lastfm)
    if not candidates:
        raise NoResultsError(query)

    candidates = candidates[:5]
    scoring_candidates = [
        candidate.model_copy(update={"tags": candidate.tags or candidate.fallback_tags})
        for candidate in candidates
    ]
    best_idx = await score_and_select(query, parsed, scoring_candidates, llm, use_llm=use_llm)
    selected = candidates[best_idx]

    if parsed.type == "mood":
        enriched = selected.model_copy(update={"tags": selected.fallback_tags})
        return enriched.to_track()

    [enriched] = await enrich_all([selected], lastfm)
    return enriched.to_track()


async def enrich_all(candidates: list[CandidateTrack], lastfm: LastFmClient) -> list[CandidateTrack]:
    results = await asyncio.gather(
        *[_track_top_tags_with_fallback(candidate, lastfm) for candidate in candidates],
        return_exceptions=True,
    )

    enriched: list[CandidateTrack] = []
    for candidate, tags in zip(candidates, results, strict=False):
        if isinstance(tags, Exception):
            if isinstance(tags, ServiceUnavailableError):
                raise tags
            enriched.append(candidate.model_copy(update={"tags": []}))
        else:
            enriched.append(candidate.model_copy(update={"tags": tags}))
    return enriched


async def _track_top_tags_with_fallback(candidate: CandidateTrack, lastfm: LastFmClient):
    lookup_pairs = [
        *candidate.lastfm_candidates,
        *[
            pair
            for pair in [candidate_lookup_pair(candidate)]
            if pair not in candidate.lastfm_candidates
        ],
    ]

    for pair in lookup_pairs:
        try:
            tags = await asyncio.wait_for(
                lastfm.track_top_tags(pair.artist, pair.title),
                timeout=LASTFM_TAG_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            tags = []
        if tags:
            return merge_tags(candidate.fallback_tags, tags)

    return candidate.fallback_tags


def candidate_lookup_pair(candidate: CandidateTrack):
    return LastFmLookup(artist=candidate.artist, title=candidate.title)


def merge_tags(preferred: list[Tag], discovered: list[Tag]) -> list[Tag]:
    merged: list[Tag] = []
    seen: set[str] = set()
    for tag in [*preferred, *discovered]:
        key = tag.name.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(tag)
    return merged[:8]
