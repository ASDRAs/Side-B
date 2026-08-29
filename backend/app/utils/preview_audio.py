import asyncio
import io
import subprocess
from dataclasses import dataclass
from typing import Literal

import httpx
import librosa
import numpy as np

from app.llm.llm_response import TrackSearchAnalysis
from app.llm.llm_wrapper import GeminiWrapper
from app.llm.prompt import TRACK_SEARCH_ANALYSIS_PROMPT
from app.services.catalog import CatalogClient

Provider = Literal["itunes", "deezer"]

MAX_PREVIEW_BYTES = 10 * 1024 * 1024

# 동시에 너무 많은 오디오를 디코딩하지 않도록 제한
_DECODE_SEMAPHORE = asyncio.Semaphore(4)


class PreviewNotFoundError(Exception):
    pass


class PreviewDownloadError(Exception):
    pass


class PreviewDecodeError(Exception):
    pass


@dataclass(frozen=True)
class PreviewCandidate:
    provider: Provider
    preview_url: str
    track_name: str
    artist: str


@dataclass(frozen=True)
class LoadedPreview:
    audio: np.ndarray
    sample_rate: int
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


async def _search_itunes(
    catalog: CatalogClient,
    track_name: str,
    artist: str,
) -> PreviewCandidate | None:
    result = await catalog.itunes_search_best(
        track_name=track_name,
        artist=artist,
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
        track_name=str(result.get("trackName") or track_name),
        artist=str(result.get("artistName") or artist),
    )


async def _search_deezer(
    catalog: CatalogClient,
    track_name: str,
    artist: str,
) -> PreviewCandidate | None:
    result = await catalog.deezer_search_best(
        track_name=track_name,
        artist=artist,
    )

    if not result:
        return None

    preview_url = str(result.get("preview") or "")

    if not preview_url:
        return None

    artist_payload = result.get("artist")

    resolved_artist = (
        str(artist_payload.get("name") or artist)
        if isinstance(artist_payload, dict)
        else artist
    )

    return PreviewCandidate(
        provider="deezer",
        preview_url=preview_url,
        track_name=str(result.get("title") or track_name),
        artist=resolved_artist,
    )


async def _get_preview_bytes(
    preview_url: str,
    http: httpx.AsyncClient,
) -> bytes:
    response = await http.get(
        preview_url,
        headers={
            "Accept": "audio/*,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0",
        },
        follow_redirects=True,
        timeout=30.0,
    )
    response.raise_for_status()

    audio_bytes = response.content

    if not audio_bytes:
        raise PreviewDownloadError("preview response is empty")

    if len(audio_bytes) > MAX_PREVIEW_BYTES:
        raise PreviewDownloadError(
            f"preview response is too large: {len(audio_bytes)} bytes"
        )

    return audio_bytes


def _convert_to_wav(
    audio_bytes: bytes,
    sample_rate: int,
) -> bytes:
    """M4A/AAC같이 librosa가 BytesIO에서 읽지 못하는 형식을 WAV로 변환."""

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-f",
                "wav",
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "pipe:1",
            ],
            input=audio_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise PreviewDecodeError(
            "ffmpeg is required to decode iTunes preview audio"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PreviewDecodeError("ffmpeg decoding timed out") from exc
    except subprocess.CalledProcessError as exc:
        error_message = exc.stderr.decode(
            "utf-8",
            errors="replace",
        )

        raise PreviewDecodeError(f"ffmpeg decoding failed: {error_message}") from exc

    return result.stdout


def _load_audio(
    audio_bytes: bytes,
    sample_rate: int,
) -> tuple[np.ndarray, int]:
    """오디오 bytes를 디스크에 저장하지 않고 np.ndarray로 변환합니다."""

    try:
        audio, loaded_sample_rate = librosa.load(
            io.BytesIO(audio_bytes),
            sr=sample_rate,
            mono=True,
            dtype=np.float32,
        )
    except Exception:
        # iTunes preview는 WAV로 변경
        wav_bytes = _convert_to_wav(
            audio_bytes,
            sample_rate,
        )

        try:
            audio, loaded_sample_rate = librosa.load(
                io.BytesIO(wav_bytes),
                sr=sample_rate,
                mono=True,
                dtype=np.float32,
            )
        except Exception as exc:
            raise PreviewDecodeError("failed to load preview audio") from exc

    audio = np.asarray(audio, dtype=np.float32)

    if audio.size == 0:
        raise PreviewDecodeError("decoded preview audio is empty")

    return audio, loaded_sample_rate


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


async def load_track_preview(
    track_name: str,
    artist: str,
    http: httpx.AsyncClient,
    gemini_wrapper: GeminiWrapper,
    sample_rate: int = 48_000,
) -> LoadedPreview:
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

                async with _DECODE_SEMAPHORE:
                    audio, loaded_sample_rate = await asyncio.to_thread(
                        _load_audio,
                        audio_bytes,
                        sample_rate,
                    )

                return LoadedPreview(
                    audio=audio,
                    sample_rate=loaded_sample_rate,
                    provider=candidate.provider,
                    track_name=candidate.track_name,
                    artist=candidate.artist,
                )

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
