import asyncio
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.genre_classification.audio_preprocessing import split_audio
from app.genre_classification.classifier import predict_by_svm
from app.genre_classification.embedder import extract_clap_embedding
from app.genre_classification.schema import GenreModels
from app.llm.llm_wrapper import GeminiWrapper
from app.utils.preview_audio import LoadedPreview, load_track_preview

SAMPLE_RATE = 48_000
TOTAL_SECONDS = 30
CHUNK_SECONDS = 10
_INFERENCE_SEMAPHORE = asyncio.Semaphore(1)


class GenreClassificationConfigurationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class GenreClassificationResult:
    track_name: str
    artist: str
    genre: str
    score: float


def _classify_preview(
    preview: LoadedPreview,
    models: GenreModels,
) -> tuple[str, float]:
    chunks = split_audio(
        preview.audio,
        preview.sample_rate,
        TOTAL_SECONDS,
        CHUNK_SECONDS,
    )

    embedding = extract_clap_embedding(
        audio_chunks=chunks,
        models=models,
        sample_rate=preview.sample_rate,
    )

    prediction = predict_by_svm(
        embedding=embedding,
        models=models,
    )

    return prediction["genre"], prediction["score"]


async def run_genre_classification(
    track_name: str,
    artist: str,
    http: httpx.AsyncClient,
    settings: Settings,
    models: GenreModels,
) -> dict:
    """
    유저의 free-form query를 받아 노래를 추천합니다.
    """
    gemini_wrapper = GeminiWrapper(settings.gemini_api_key, settings.gemini_model)
    async with httpx.AsyncClient() as http:
        preview = await load_track_preview(
            track_name=track_name,
            artist=artist,
            http=http,
            gemini_wrapper=gemini_wrapper,
            sample_rate=SAMPLE_RATE,
        )

    async with _INFERENCE_SEMAPHORE:
        genre, score = await asyncio.to_thread(
            _classify_preview,
            preview,
            models,
        )

    return GenreClassificationResult(
        track_name=track_name,
        artist=artist,
        genre=genre,
        score=score,
    )
