import asyncio
import logging
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.routers.auth import router as auth_router
from app.routers.genre_classification import router as genre_router
from app.routers.recommend import router as recommend_router
from app.routers.youtube_export import router as youtube_router
from app.services.auth import (
    AuthenticationError,
    AuthenticationService,
    AuthenticationUnavailableError,
    FeatureRateLimiter,
    FeatureRateLimitError,
    FirebaseTokenVerifier,
)
from app.services.youtube.matcher import MatchOutcome

PROJECT_ID = "side-b-test-project"


def claims(
    uid="allowed-uid",
    email="allowed@example.com",
    *,
    provider="google.com",
    verified=True,
    project_id=PROJECT_ID,
):
    return {
        "aud": project_id,
        "iss": f"https://securetoken.google.com/{project_id}",
        "uid": uid,
        "sub": uid,
        "email": email,
        "email_verified": verified,
        "name": "Allowed User",
        "firebase": {"sign_in_provider": provider},
    }


def verifier_transport(token):
    if token in {"expired", "revoked", "disabled", "invalid"}:
        raise AuthenticationError(f"{token} token")
    if token == "outage":
        raise AuthenticationUnavailableError("certificate fetch failed")
    if token == "wrong-project":
        return claims(project_id="another-project")
    if token == "wrong-provider":
        return claims(provider="password")
    if token == "unverified":
        return claims(verified=False)
    if token == "disallowed":
        return claims(uid="other-uid", email="other@example.com")
    return claims()


def auth_service(
    *, mode="firebase", project_id=PROJECT_ID, allowed=True, legacy_token="legacy"
):
    return AuthenticationService(
        mode=mode,
        legacy_token=legacy_token,
        firebase_verifier=FirebaseTokenVerifier(
            project_id, verify_transport=verifier_transport
        ),
        allowed_uids=frozenset({"allowed-uid"}) if allowed else frozenset(),
        allowed_emails=frozenset(),
    )


def limiter(user_limit=10, aggregate_limit=100, **kwargs):
    return FeatureRateLimiter(
        user_limits={
            "recommend": user_limit,
            "genre": user_limit,
            "youtube_export": user_limit,
        },
        aggregate_limits={
            "recommend": aggregate_limit,
            "genre": aggregate_limit,
            "youtube_export": aggregate_limit,
        },
        **kwargs,
    )


def make_auth_app(service=None):
    app = FastAPI()
    app.include_router(auth_router)
    app.state.auth_service = service or auth_service()
    app.state.feature_rate_limiter = limiter()
    return app


async def get(app, path, headers=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        return await client.get(path, headers=headers)


async def post(app, path, payload, headers=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        return await client.post(path, json=payload, headers=headers)


async def test_auth_config_is_public_and_contains_no_credentials():
    response = await get(make_auth_app(), "/auth/config")

    assert response.status_code == 200
    assert response.json() == {
        "mode": "firebase",
        "firebase_project_id": PROJECT_ID,
    }
    assert "token" not in response.text.casefold()


async def test_auth_me_returns_only_minimal_verified_identity():
    response = await get(make_auth_app(), "/auth/me", {"Authorization": "Bearer valid"})

    assert response.status_code == 200
    assert response.json() == {
        "uid": "allowed-uid",
        "email": "allowed@example.com",
        "display_name": "Allowed User",
    }


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Basic value",
        "Bearer",
        "Bearer expired",
        "Bearer revoked",
        "Bearer disabled",
    ],
)
async def test_missing_malformed_expired_revoked_and_disabled_tokens_fail_closed(
    authorization,
):
    headers = {"Authorization": authorization} if authorization else None
    response = await get(make_auth_app(), "/auth/me", headers)

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "auth_unauthorized"


async def test_wrong_project_token_is_rejected_after_verification_boundary():
    response = await get(
        make_auth_app(),
        "/auth/me",
        {"Authorization": "Bearer wrong-project"},
    )

    assert response.status_code == 401


@pytest.mark.parametrize("token", ["wrong-provider", "unverified", "disallowed"])
async def test_google_provider_verified_email_and_allowlist_are_required(token):
    response = await get(
        make_auth_app(), "/auth/me", {"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "auth_account_denied"


async def test_empty_allowlist_denies_every_verified_account():
    response = await get(
        make_auth_app(auth_service(allowed=False)),
        "/auth/me",
        {"Authorization": "Bearer valid"},
    )

    assert response.status_code == 403


async def test_verification_outage_is_503_and_token_is_not_logged(caplog):
    caplog.set_level(logging.DEBUG)
    response = await get(
        make_auth_app(), "/auth/me", {"Authorization": "Bearer outage"}
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "auth_verification_unavailable"
    assert "outage" not in caplog.text


def test_firebase_sdk_call_is_project_bound_revocation_checked_and_timeout_bounded(
    monkeypatch,
):
    import firebase_admin
    from firebase_admin import auth

    initialized = {}
    app = object()

    def missing_app(name):
        raise ValueError(name)

    def initialize(*, options, name):
        initialized.update(options=options, name=name)
        return app

    calls = []

    def verify(token, *, app, check_revoked):
        calls.append((token, app, check_revoked))
        return claims()

    monkeypatch.setattr(firebase_admin, "get_app", missing_app)
    monkeypatch.setattr(firebase_admin, "initialize_app", initialize)
    monkeypatch.setattr(auth, "verify_id_token", verify)
    verifier = FirebaseTokenVerifier(PROJECT_ID, http_timeout_seconds=4.0)

    assert verifier._verify("sdk-token")["uid"] == "allowed-uid"
    assert initialized["options"] == {
        "projectId": PROJECT_ID,
        "httpTimeout": 4.0,
    }
    assert calls == [("sdk-token", app, True)]


@pytest.mark.parametrize(
    ("sdk_error", "expected"),
    [
        ("invalid", AuthenticationError),
        ("certificate", AuthenticationUnavailableError),
        ("deleted_user", AuthenticationError),
    ],
)
def test_firebase_sdk_errors_are_mapped_at_the_direct_boundary(
    monkeypatch, sdk_error, expected
):
    from firebase_admin import auth

    verifier = FirebaseTokenVerifier(PROJECT_ID)
    verifier._app = object()
    error = (
        auth.InvalidIdTokenError("invalid")
        if sdk_error == "invalid"
        else auth.CertificateFetchError("unavailable", OSError("offline"))
    )
    if sdk_error == "deleted_user":
        error = auth.UserNotFoundError("deleted")
    monkeypatch.setattr(
        auth, "verify_id_token", lambda *args, **kwargs: (_ for _ in ()).throw(error)
    )

    with pytest.raises(expected):
        verifier._verify("sdk-token")


async def test_verification_slot_stays_occupied_until_timed_out_worker_finishes():
    release = threading.Event()
    calls = []

    def slow(token):
        calls.append(token)
        if token == "slow":
            release.wait(2)
        return claims()

    verifier = FirebaseTokenVerifier(
        PROJECT_ID,
        verify_transport=slow,
        max_concurrency=1,
    )
    verifier._verify_timeout_seconds = 0.02

    with pytest.raises(AuthenticationUnavailableError):
        await verifier.verify("slow")
    with pytest.raises(AuthenticationUnavailableError):
        await verifier.verify("queued")
    assert calls == ["slow"]

    release.set()
    await asyncio.sleep(0.05)
    assert (await verifier.verify("after"))["uid"] == "allowed-uid"


async def test_missing_firebase_project_configuration_fails_closed():
    response = await get(
        make_auth_app(auth_service(project_id="")),
        "/auth/me",
        {"Authorization": "Bearer valid"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "auth_configuration_error"


async def test_firebase_only_rejects_legacy_and_dual_never_downgrades_bearer():
    firebase = await get(
        make_auth_app(), "/auth/me", {"X-Side-B-Access-Token": "legacy"}
    )
    dual = await get(
        make_auth_app(auth_service(mode="dual")),
        "/auth/me",
        {
            "Authorization": "Bearer invalid",
            "X-Side-B-Access-Token": "legacy",
        },
    )

    assert firebase.status_code == 401
    assert dual.status_code == 401


async def test_explicit_legacy_mode_remains_available():
    response = await get(
        make_auth_app(auth_service(mode="legacy")),
        "/auth/me",
        {"X-Side-B-Access-Token": "legacy"},
    )

    assert response.status_code == 200
    assert response.json()["uid"] == "legacy-shared"


async def test_user_feature_limits_are_isolated_with_aggregate_capacity():
    clock = [0.0]
    rate = limiter(user_limit=1, aggregate_limit=10, clock=lambda: clock[0])

    await rate.consume("user-a", "recommend")
    await rate.consume("user-a", "genre")
    await rate.consume("user-b", "recommend")
    with pytest.raises(FeatureRateLimitError):
        await rate.consume("user-a", "recommend")


async def test_aggregate_limit_is_a_separate_backstop():
    rate = limiter(user_limit=5, aggregate_limit=2)

    await rate.consume("user-a", "recommend")
    await rate.consume("user-b", "recommend")
    with pytest.raises(FeatureRateLimitError):
        await rate.consume("user-c", "recommend")


async def test_inactive_bucket_cleanup_is_bounded_without_evicting_active_buckets():
    clock = [0.0]
    rate = limiter(
        user_limit=1,
        aggregate_limit=10,
        clock=lambda: clock[0],
        window_seconds=60,
        inactive_ttl_seconds=60,
        max_user_buckets=1,
    )
    await rate.consume("user-a", "recommend")
    with pytest.raises(FeatureRateLimitError):
        await rate.consume("user-b", "recommend")
    with pytest.raises(FeatureRateLimitError):
        await rate.consume("user-a", "recommend")

    clock[0] = 61
    await rate.consume("user-b", "recommend")
    assert rate.user_bucket_count == 1


async def test_all_protected_routes_use_the_real_auth_dependency(monkeypatch):
    verified_tokens = []

    def transport(token):
        verified_tokens.append(token)
        return claims()

    service = AuthenticationService(
        mode="firebase",
        legacy_token=None,
        firebase_verifier=FirebaseTokenVerifier(PROJECT_ID, verify_transport=transport),
        allowed_uids=frozenset({"allowed-uid"}),
    )
    app = FastAPI()
    app.include_router(recommend_router)
    app.include_router(genre_router)
    app.include_router(youtube_router)
    app.state.auth_service = service
    app.state.feature_rate_limiter = limiter()
    app.state.http = None
    app.state.lastfm_pylast = None
    app.state.settings = SimpleNamespace()
    app.state.genre_inference = object()

    async def fake_recommend(*args, **kwargs):
        return {
            "track_name": "Seed",
            "artist": "Artist",
            "top_n": 10,
            "result": {"similar": [], "reverse": [], "hidden": []},
        }

    async def fake_genre(*args, **kwargs):
        return SimpleNamespace(
            track_name="Seed",
            artist="Artist",
            genre="pop",
            score=1.0,
            model_version="test",
        )

    class Matcher:
        async def match_track(self, name, artist):
            return MatchOutcome(match=None, reason="not_found")

    app.state.youtube_matcher = Matcher()
    monkeypatch.setattr("app.routers.recommend.run_recommend", fake_recommend)
    monkeypatch.setattr("app.routers.recommend.get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "app.routers.genre_classification.run_genre_classification", fake_genre
    )
    headers = {"Authorization": "Bearer valid"}

    responses = [
        await post(app, "/recommend", {"query": "Seed"}, headers),
        await post(
            app,
            "/genre-classification",
            {"track_name": "Seed", "artist": "Artist"},
            headers,
        ),
        await post(
            app,
            "/exports/youtube/matches",
            {
                "bucket": "similar",
                "tracks": [{"name": "Seed", "artist": "Artist"}],
            },
            headers,
        ),
    ]

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert verified_tokens == ["valid", "valid", "valid"]
