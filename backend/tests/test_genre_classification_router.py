from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.routers.genre_classification import router
from app.services.access import BackendAccess
from app.services.inference_client import InferenceClient
from app.utils.preview_audio import PreviewBytes, PreviewNotFoundError


@pytest.mark.parametrize("status,expected", [(200, 200), (422, 422), (503, 503)])
async def test_route_resolves_audio_then_calls_private_service(
    monkeypatch, status, expected
):
    async def preview(**kwargs):
        assert kwargs["track_name"] == "Input title"
        return PreviewBytes(
            b"resolved-audio", "itunes", "Resolved title", "Resolved artist"
        )

    monkeypatch.setattr(
        "app.services.genre_classification_service.load_track_preview_bytes", preview
    )

    def infer(request):
        assert request.content == b"resolved-audio"
        return httpx.Response(
            status, json={"genre": "pop", "score": -0.4, "model_version": "v1"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(infer)) as http:
        app = make_app(http)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as caller:
            response = await caller.post(
                "/genre-classification",
                json={"track_name": "Input title", "artist": "Input artist"},
                headers={"X-Side-B-Access-Token": "team-token"},
            )
    assert response.status_code == expected
    if expected == 200:
        assert response.json() == {
            "track_name": "Resolved title",
            "artist": "Resolved artist",
            "genre": "pop",
            "score": -0.4,
            "model_version": "v1",
        }


def make_app(http, url="http://localhost:8082", limit=6):
    app = FastAPI()
    app.include_router(router)
    app.state.http = http
    app.state.settings = SimpleNamespace(
        gemini_api_key="test-key", gemini_model="gemini-2.5-flash"
    )
    app.state.genre_inference = InferenceClient(http, url, use_iam=False)
    app.state.recommend_access = BackendAccess("team-token")
    app.state.genre_access = BackendAccess("team-token", requests_per_minute=limit)
    return app


async def _post(app, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as caller:
        return await caller.post(
            "/genre-classification",
            json={"track_name": "Track", "artist": "Artist"},
            headers={"X-Side-B-Access-Token": "team-token"},
            **kwargs,
        )


async def test_genre_rate_limit_does_not_consume_the_recommend_budget(monkeypatch):
    import main
    from app.config import Settings

    settings = Settings(
        _env_file=None,
        SIDE_B_ACCESS_TOKEN="team-token",
        GENRE_REQUESTS_PER_MINUTE=1,
        RECOMMEND_REQUESTS_PER_MINUTE=1,
        GEMINI_API_KEY="fixture",
        CLAP_INFERENCE_URL="http://localhost:8082",
        CLAP_INFERENCE_USE_IAM=False,
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr("app.routers.recommend.get_settings", lambda: settings)

    async def miss(**kwargs):
        raise PreviewNotFoundError("not found")

    async def recommend(*args):
        return {"track_name": "Track", "artist": "Artist", "top_n": 10, "result": {}}

    monkeypatch.setattr("app.routers.recommend.run_recommend", recommend)
    monkeypatch.setattr(
        "app.services.genre_classification_service.load_track_preview_bytes", miss
    )
    async with main.app.router.lifespan_context(main.app):
        assert main.app.state.genre_access is not main.app.state.recommend_access
        assert (await _post(main.app)).status_code == 404
        assert (await _post(main.app)).status_code == 429
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(main.app), base_url="http://test"
        ) as caller:
            headers = {"X-Side-B-Access-Token": "team-token"}
            response = await caller.post(
                "/recommend", json={"query": "Track"}, headers=headers
            )
            assert response.status_code == 200
            assert (
                await caller.post(
                    "/recommend", json={"query": "Track"}, headers=headers
                )
            ).status_code == 429


async def test_unconfigured_inference_is_unavailable_not_a_boot_failure():
    async with httpx.AsyncClient() as http:
        app = make_app(http)
        app.state.genre_inference = None
        result = await _post(app)
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "genre_configuration_error"


@pytest.mark.parametrize(
    "token,url,expected", [(None, "http://localhost", 401), ("team-token", "", 503)]
)
async def test_route_configuration_and_auth_fail_before_network(token, url, expected):
    def unexpected(request):
        pytest.fail("Network should not be called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
        app = make_app(http, url)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as caller:
            headers = {"X-Side-B-Access-Token": token} if token else {}
            result = await caller.post(
                "/genre-classification",
                json={"track_name": "Track", "artist": "Artist"},
                headers=headers,
            )
    assert result.status_code == expected


async def test_preview_miss_is_not_an_inference_failure(monkeypatch):
    async def miss(**kwargs):
        raise PreviewNotFoundError("not found")

    monkeypatch.setattr(
        "app.services.genre_classification_service.load_track_preview_bytes", miss
    )
    async with httpx.AsyncClient() as http:
        app = make_app(http)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as caller:
            result = await caller.post(
                "/genre-classification",
                json={"track_name": "Track", "artist": "Artist"},
                headers={"X-Side-B-Access-Token": "team-token"},
            )
    assert result.status_code == 404
    assert result.json()["detail"]["code"] == "preview_not_found"
