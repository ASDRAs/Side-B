from dataclasses import dataclass

import httpx

from app.config import Settings
from app.llm.llm_wrapper import GeminiWrapper
from app.services.inference_client import InferenceClient, InferenceConfigurationError
from app.utils.preview_audio import load_track_preview_bytes

GenreClassificationConfigurationError = InferenceConfigurationError


@dataclass(frozen=True, slots=True)
class GenreClassificationResult:
    track_name: str
    artist: str
    genre: str
    score: float
    model_version: str


async def run_genre_classification(
    track_name: str,
    artist: str,
    http: httpx.AsyncClient,
    settings: Settings,
    inference: InferenceClient | None,
) -> GenreClassificationResult:
    if inference is None or not inference.url or not settings.gemini_api_key:
        raise GenreClassificationConfigurationError(
            "Genre classification is not configured"
        )
    gemini_wrapper = GeminiWrapper(settings.gemini_api_key, settings.gemini_model)
    preview = await load_track_preview_bytes(
        track_name=track_name,
        artist=artist,
        http=http,
        gemini_wrapper=gemini_wrapper,
    )
    prediction = await inference.predict(preview.audio_bytes)

    return GenreClassificationResult(
        track_name=preview.track_name,
        artist=preview.artist,
        genre=prediction.genre,
        score=prediction.score,
        model_version=prediction.model_version,
    )
