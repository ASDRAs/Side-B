from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

import httpx

from app.llm.llm_response import TrackSearchAnalysis
from app.llm.llm_wrapper import GeminiWrapper
from app.llm.prompt import TRACK_SEARCH_ANALYSIS_PROMPT
from app.services.catalog import (
    CatalogClient,
    DeezerRateLimitError,
    ItunesRateLimitError,
)

Provider = Literal["itunes", "deezer"]

MAX_PREVIEW_BYTES = 10 * 1024 * 1024


class PreviewNotFoundError(Exception):
    pass


class PreviewDownloadError(Exception):
    pass


@dataclass(frozen=True)
class PreviewCandidate:
    provider: Provider
    preview_url: str
    track_name: str
    artist: str


@dataclass(frozen=True)
class PreviewBytes:
    audio_bytes: bytes
    provider: Provider
    track_name: str
    artist: str


async def analyze_track_search(
    track_name: str,
    artist: str,
    gemini_wrapper: GeminiWrapper,
) -> TrackSearchAnalysis:
    result = await asyncio.to_thread(
        gemini_wrapper.request,
        system_prompt=TRACK_SEARCH_ANALYSIS_PROMPT,
        user_prompt=(f"track_title: {track_name}\nartist: {artist}"),
        temperature=0.0,
        max_output_tokens=300,
        response_schema=TrackSearchAnalysis,
        response_validator=TrackSearchAnalysis,
    )

    if isinstance(result, TrackSearchAnalysis):
        return result

    return TrackSearchAnalysis.model_validate_json(result)


async def _get_preview_bytes(
    preview_url: str,
    http: httpx.AsyncClient,
) -> bytes:
    async with http.stream(
        "GET",
        preview_url,
        headers={
            "Accept": "audio/*,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0",
        },
        follow_redirects=True,
        timeout=30.0,
    ) as response:
        response.raise_for_status()
        data = bytearray()
        async for chunk in response.aiter_bytes():
            if len(data) + len(chunk) > MAX_PREVIEW_BYTES:
                raise PreviewDownloadError("preview exceeds 10 MiB")
            data.extend(chunk)

    audio_bytes = bytes(data)

    if not audio_bytes:
        raise PreviewDownloadError("preview response is empty")

    if len(audio_bytes) > MAX_PREVIEW_BYTES:
        raise PreviewDownloadError(
            f"preview response is too large: {len(audio_bytes)} bytes"
        )

    return audio_bytes


async def _search_preview_candidate(
    catalog: CatalogClient,
    provider: Provider,
    track_title: str,
    artist_name: str,
) -> PreviewCandidate | None:
    if provider == "itunes":
        result = await catalog.itunes_search_best(
            track_name=track_title,
            artist=artist_name,
            limit=10,
            min_score=0.72,
            min_artist_score=0.7,
        )

        if not result:
            return None

        preview_url = str(result.get("previewUrl") or "")

        if not preview_url:
            return None

        return PreviewCandidate(
            provider="itunes",
            preview_url=preview_url,
            track_name=str(result.get("trackName") or track_title),
            artist=str(result.get("artistName") or artist_name),
        )

    result = await catalog.deezer_search_best(
        track_name=track_title,
        artist=artist_name,
    )

    if not result:
        return None

    preview_url = str(result.get("preview") or "")

    if not preview_url:
        return None

    artist_payload = result.get("artist")

    resolved_artist = (
        str(artist_payload.get("name") or artist_name)
        if isinstance(artist_payload, dict)
        else artist_name
    )

    return PreviewCandidate(
        provider="deezer",
        preview_url=preview_url,
        track_name=str(result.get("title") or track_title),
        artist=resolved_artist,
    )


async def load_track_preview_bytes(
    track_name: str,
    artist: str,
    http: httpx.AsyncClient,
    gemini_wrapper: GeminiWrapper,
) -> PreviewBytes:
    analysis = await analyze_track_search(
        track_name=track_name,
        artist=artist,
        gemini_wrapper=gemini_wrapper,
    )

    provider_order: tuple[Provider, Provider] = (
        ("itunes", "deezer") if analysis.country == "korea" else ("deezer", "itunes")
    )

    catalog = CatalogClient(http)
    errors: list[str] = []

    # 주 공급자에서 검색어 3개를 모두 시도한 뒤
    # 보조 공급자에서 다시 검색합니다.
    for provider in provider_order:
        for search_query in analysis.search_queries:
            try:
                candidate = await _search_preview_candidate(
                    catalog=catalog,
                    provider=provider,
                    track_title=search_query.track_title,
                    artist_name=search_query.artist_name,
                )

                if candidate is None:
                    errors.append(
                        f"{provider}: "
                        f"{search_query.artist_name} - "
                        f"{search_query.track_title}: not found"
                    )
                    continue

                audio_bytes = await _get_preview_bytes(
                    candidate.preview_url,
                    http,
                )

                return PreviewBytes(
                    audio_bytes=audio_bytes,
                    provider=candidate.provider,
                    track_name=candidate.track_name,
                    artist=candidate.artist,
                )

            except (ItunesRateLimitError, DeezerRateLimitError) as exc:
                # The provider gate already blocks further calls. Skip the
                # remaining queries for this provider and try the next one.
                errors.append(f"{provider}: rate limited for {exc.retry_after}s")
                break

            except Exception as exc:
                errors.append(
                    f"{provider}: "
                    f"{search_query.artist_name} - "
                    f"{search_query.track_title}: "
                    f"{type(exc).__name__}: {exc}"
                )

    raise PreviewNotFoundError(
        f"preview unavailable for {artist} - {track_name}: " + "; ".join(errors)
    )
