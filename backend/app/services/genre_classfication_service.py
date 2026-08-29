import httpx

from app.config import Settings
from app.genre_classfication.audio_preprocessing import split_audio
from app.llm.llm_wrapper import GeminiWrapper
from app.utils.preview_audio import load_track_preview


async def run_genre_classfier(
    track_name: str,
    artist: int,
    http: httpx.AsyncClient,
    settings: Settings,
) -> dict:
    """
    유저의 free-form query를 받아 노래를 추천합니다.
    """
    SAMPLE_RATE = 48_000
    TOTAL_SECONDS = 30
    CHUNK_SECONDS = 10
    gemini_wrapper = GeminiWrapper(settings.gemini_api_key, settings.gemini_model)
    async with httpx.AsyncClient() as http:
        result = await load_track_preview(
            track_name="Beautiful Things",
            artist="Benson James Boone",
            http=http,
            gemini_wrapper=gemini_wrapper,
            sample_rate=SAMPLE_RATE,
        )
    chunks = split_audio(result.audio, SAMPLE_RATE, TOTAL_SECONDS, CHUNK_SECONDS)
    # embeddings =
