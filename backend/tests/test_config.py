from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from main import app

ROOT_DIR = Path(__file__).resolve().parents[2]


def test_backend_access_token_prefers_generic_name_and_supports_legacy_alias():
    current = Settings(
        _env_file=None,
        SIDE_B_ACCESS_TOKEN="current-token",
        YOUTUBE_EXPORT_TOKEN="legacy-token",
    )
    legacy = Settings(_env_file=None, YOUTUBE_EXPORT_TOKEN="legacy-token")

    assert current.backend_access_token == "current-token"
    assert legacy.backend_access_token == "legacy-token"


def test_example_env_keeps_unauthenticated_recommend_disabled(monkeypatch):
    monkeypatch.delenv("ALLOW_UNAUTHENTICATED_RECOMMEND", raising=False)

    settings = Settings(_env_file=ROOT_DIR / ".env.example")

    assert settings.allow_unauthenticated_recommend is False


def test_default_cors_allowlist_is_explicit():
    settings = Settings(_env_file=None)

    assert "*" not in settings.cors_origin_allowlist
    assert (
        "chrome-extension://hfcclomfoickmehgmdgjdjmiiekaciam"
        in settings.cors_origin_allowlist
    )
    assert "http://127.0.0.1:3000" in settings.cors_origin_allowlist


def test_cors_allowlist_rejects_wildcard():
    with pytest.raises(ValidationError, match="must not contain"):
        Settings(_env_file=None, CORS_ALLOWED_ORIGINS="*")


def test_cors_allows_fixed_extension_and_rejects_untrusted_web_origin():
    headers = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-side-b-access-token",
    }
    with TestClient(app) as client:
        allowed = client.options(
            "/recommend",
            headers={
                **headers,
                "Origin": "chrome-extension://hfcclomfoickmehgmdgjdjmiiekaciam",
            },
        )
        denied = client.options(
            "/recommend",
            headers={**headers, "Origin": "https://attacker.example"},
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == (
        "chrome-extension://hfcclomfoickmehgmdgjdjmiiekaciam"
    )
    assert "access-control-allow-origin" not in denied.headers


def test_invalid_inference_url_disables_genre_only_and_still_boots(monkeypatch):
    import main

    settings = Settings(
        _env_file=None,
        SIDE_B_ACCESS_TOKEN="team-token",
        CLAP_INFERENCE_URL="http://inference.example.com/predict",
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    with TestClient(main.app) as client:
        assert client.get("/api/health").status_code == 200
        assert main.app.state.genre_inference is None
        # 추천 경로는 계속 살아 있어야 한다.
        assert main.app.state.recommend_access is not None
