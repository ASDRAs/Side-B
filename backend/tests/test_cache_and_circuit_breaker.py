from unittest.mock import AsyncMock
import pytest

from app.services.catalog import CatalogClient, DeezerRateLimitError


async def test_deezer_429_propagates_rate_limit_error():
    mock_response = AsyncMock()
    mock_response.status_code = 429
    mock_response.headers = {"Retry-After": "30"}

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=mock_response)

    catalog = CatalogClient(mock_http)
    with pytest.raises(DeezerRateLimitError) as exc_info:
        await catalog.deezer_search_best("some track", "some artist")
    assert exc_info.value.retry_after == 30


def test_artist_top_cache_keys_differ_by_limit():
    artist = "radiohead"
    assert f"lf:artist_top:{artist}:20" != f"lf:artist_top:{artist}:4"


def test_itunes_cache_keys_differ_by_min_score():
    term = "some track"
    limit = 5
    assert f"itunes:{term}:{limit}:0.35" != f"itunes:{term}:{limit}:0.65"
