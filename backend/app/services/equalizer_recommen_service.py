import httpx
from backend.app.genre_classification.classifier import predict_by_svm
from backend.app.genre_classification.schema import GenreModels

from app.config import Settings
from app.genre_classification.audio_preprocessing import split_audio
from app.genre_classification.embedder import extract_clap_embedding
from app.llm.llm_wrapper import GeminiWrapper
from app.utils.preview_audio import load_track_preview

SAMPLE_RATE = 48_000
TOTAL_SECONDS = 30
CHUNK_SECONDS = 10
EQ_PRESET = {
    "jazz": [0],
    "rock_metal": [0],
    "jpop": [0],
    "hiphop": [0],
    "rnb_soul": [0],
    "folk_blues_country": [0],
    "pop": [0],
    "dance": [0],
    "ballad": [0],
}


async def run_genre_classifier(
    track_name: str,
    artist: int,
    http: httpx.AsyncClient,
    settings: Settings,
    models: GenreModels,
) -> dict:
    """
    유저의 free-form query를 받아 노래를 추천합니다.
    """
    gemini_wrapper = GeminiWrapper(settings.gemini_api_key, settings.gemini_model)
    async with httpx.AsyncClient() as http:
        result = await load_track_preview(
            track_name=track_name,
            artist=artist,
            http=http,
            gemini_wrapper=gemini_wrapper,
            sample_rate=SAMPLE_RATE,
        )
    chunks = split_audio(result.audio, SAMPLE_RATE, TOTAL_SECONDS, CHUNK_SECONDS)
    embedding = extract_clap_embedding(chunks, models, SAMPLE_RATE)
    genre_prediction = predict_by_svm(embedding, models)

    return {
        "genre": genre_prediction["genre"],
        "score": genre_prediction["score"],
        "eq_preset": EQ_PRESET[genre_prediction["genre"]],
    }
