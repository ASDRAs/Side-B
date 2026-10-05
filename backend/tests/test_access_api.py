"""HTTP contract and authorization tests for the Firestore approval mode.

The store here is ``InMemoryAccessStore``: production policy functions under an
asyncio lock. These tests prove the API/permission contract, not Firestore
transaction behavior (see ``tests/integration`` for the emulator suite).
"""

import asyncio
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import main
from app.config import AccessConfigurationError, Settings
from app.routers.access import router as access_router
from app.routers.auth import router as auth_router
from app.routers.genre_classification import router as genre_router
from app.routers.recommend import router as recommend_router
from app.routers.youtube_export import router as youtube_router
from app.services.auth import (
    RATE_LIMITED_FEATURES,
    AuthenticationError,
    AuthenticationService,
    AuthenticationUnavailableError,
    FeatureRateLimiter,
    FirebaseTokenVerifier,
)
from app.services.youtube.matcher import MatchOutcome
from app.utils.body_limit import BodySizeLimitMiddleware
from preview import MediaBinding
from preview import router as preview_router
from tests.access_fakes import InMemoryAccessStore, outage

PROJECT_ID = "side-b-test-project"
ADMIN = "admin-uid"


def claims(uid, *, provider="google.com", verified=True, email=None):
    return {
        "aud": PROJECT_ID,
        "iss": f"https://securetoken.google.com/{PROJECT_ID}",
        "uid": uid,
        "sub": uid,
        "email": email or f"{uid}@example.com",
        "email_verified": verified,
        "name": f"Name {uid}",
        "firebase": {"sign_in_provider": provider},
    }


def transport(token):
    if token in {"invalid", "expired", "revoked"}:
        raise AuthenticationError(f"{token} token")
    if token == "outage":
        raise AuthenticationUnavailableError("certificate fetch failed")
    if token == "wrong-provider":
        return claims("password-user", provider="password")
    if token == "unverified":
        return claims("unverified-user", verified=False)
    if token.startswith("user-"):
        return claims(token.removeprefix("user-"))
    raise AuthenticationError("unknown fixture token")


def bearer(uid):
    return {"Authorization": f"Bearer user-{uid}"}


def rate_limiter(user_limit=100, aggregate_limit=1_000, overrides=None):
    users = dict.fromkeys(RATE_LIMITED_FEATURES, user_limit)
    aggregates = dict.fromkeys(RATE_LIMITED_FEATURES, aggregate_limit)
    for feature, (user, aggregate) in (overrides or {}).items():
        users[feature] = user
        aggregates[feature] = aggregate
    return FeatureRateLimiter(user_limits=users, aggregate_limits=aggregates)


def make_app(store=None, *, admin_uids=(ADMIN,), limiter=None, handlers=None):
    store = store if store is not None else InMemoryAccessStore(admin_uids=admin_uids)
    app = FastAPI()
    app.add_middleware(
        BodySizeLimitMiddleware, path_prefixes=("/access/", "/admin/"), max_bytes=4_096
    )
    for router in (
        auth_router,
        access_router,
        recommend_router,
        genre_router,
        youtube_router,
        preview_router,
    ):
        app.include_router(router)
    app.state.auth_service = AuthenticationService(
        mode="firebase",
        legacy_token=None,
        firebase_verifier=FirebaseTokenVerifier(PROJECT_ID, verify_transport=transport),
        enforce_allowlist=False,
    )
    app.state.admin_uids = frozenset(admin_uids)
    app.state.access_store = store
    app.state.feature_rate_limiter = limiter or rate_limiter()
    app.state.http = None
    app.state.lastfm_pylast = None
    app.state.settings = SimpleNamespace()
    app.state.genre_inference = object()
    app.state.youtube_matcher = handlers.matcher if handlers else None
    return app, store


@pytest.fixture
def feature_handlers(monkeypatch):
    calls = []

    async def fake_recommend(*args, **kwargs):
        calls.append("recommend")
        return {
            "track_name": "Seed",
            "artist": "Artist",
            "top_n": 10,
            "result": {"similar": [], "reverse": [], "hidden": []},
        }

    async def fake_genre(*args, **kwargs):
        calls.append("genre")
        return SimpleNamespace(
            track_name="Seed",
            artist="Artist",
            genre="pop",
            score=1.0,
            model_version="t",
        )

    async def fake_media(http, track, artist, provider, provider_track_id):
        calls.append("preview")
        return MediaBinding(
            provider="deezer",
            provider_track_id="1",
            preview_url="https://cdn.example/preview.mp3",
            content_type="audio/mpeg",
            file_extension="mp3",
            resolved_title="Seed",
            resolved_artist="Artist",
        )

    async def fake_stream(url, max_bytes, **kwargs):
        calls.append("stream")
        yield b"ID3fixture"

    class Matcher:
        async def match_track(self, name, artist):
            calls.append("youtube_export")
            return MatchOutcome(match=None, reason="not_found")

    monkeypatch.setattr("app.routers.recommend.run_recommend", fake_recommend)
    monkeypatch.setattr("app.routers.recommend.get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "app.routers.genre_classification.run_genre_classification", fake_genre
    )
    monkeypatch.setattr("preview._resolve_requested_media", fake_media)
    monkeypatch.setattr("preview._limited_stream", fake_stream)
    return SimpleNamespace(calls=calls, matcher=Matcher())


async def call(app, method, path, *, headers=None, payload=None, content=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        if content is not None:
            return await client.request(method, path, headers=headers, content=content)
        if payload is None:
            return await client.request(method, path, headers=headers)
        return await client.request(method, path, headers=headers, json=payload)


FEATURE_REQUESTS = [
    ("POST", "/recommend", {"query": "Seed"}, "recommend"),
    (
        "POST",
        "/genre-classification",
        {"track_name": "Seed", "artist": "Artist"},
        "genre",
    ),
    (
        "POST",
        "/exports/youtube/matches",
        {"bucket": "similar", "tracks": [{"name": "Seed", "artist": "Artist"}]},
        "youtube_export",
    ),
    ("GET", "/preview?track=Seed&artist=Artist", None, "preview"),
    ("GET", "/preview/stream?track=Seed&artist=Artist", None, "stream"),
]


def decision(action="approve", revision=1, operation_id=None, **extra):
    return {
        "action": action,
        "expected_revision": revision,
        "operation_id": operation_id or str(uuid.uuid4()),
        **extra,
    }


# ── /auth/me: identity is separate from approval ─────────────────────────


@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "blocked"])
async def test_auth_me_reports_stored_status_without_denying_identity(status):
    app, store = make_app()
    store.seed("listener", status)

    response = await call(app, "GET", "/auth/me", headers=bearer("listener"))

    assert response.status_code == 200
    body = response.json()
    assert body["uid"] == "listener"
    assert body["access_status"] == status
    assert body["access_store"] == "firestore"
    assert body["can_manage_access"] is False
    assert body["access_requested_at"].endswith("Z")


async def test_auth_me_reports_unregistered_and_admin_flag_from_server_list():
    app, _store = make_app()

    newcomer = await call(app, "GET", "/auth/me", headers=bearer("newcomer"))
    admin = await call(app, "GET", "/auth/me", headers=bearer(ADMIN))

    assert newcomer.json()["access_status"] == "unregistered"
    assert newcomer.json()["access_requested_at"] is None
    assert admin.json()["can_manage_access"] is True


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [
        (None, 401, "auth_unauthorized"),
        ({"Authorization": "Bearer invalid"}, 401, "auth_unauthorized"),
        ({"Authorization": "Bearer revoked"}, 401, "auth_unauthorized"),
        ({"X-Side-B-Access-Token": "legacy"}, 401, "auth_unauthorized"),
        (
            {"Authorization": "Bearer user-x", "X-Side-B-Access-Token": "legacy"},
            401,
            "auth_unauthorized",
        ),
        ({"Authorization": "Bearer wrong-provider"}, 403, "auth_identity_unverified"),
        ({"Authorization": "Bearer unverified"}, 403, "auth_identity_unverified"),
        ({"Authorization": "Bearer outage"}, 503, "auth_verification_unavailable"),
    ],
)
async def test_auth_me_identity_failures_never_reach_the_store(headers, status, code):
    app, store = make_app()

    response = await call(app, "GET", "/auth/me", headers=headers)

    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert store.calls == []


async def test_auth_me_store_outage_and_corrupt_record_fail_closed_with_503():
    app, store = make_app()
    store.failure = outage()
    unavailable = await call(app, "GET", "/auth/me", headers=bearer("listener"))

    store.failure = None
    store.seed("corrupt", "Approved")
    corrupt = await call(app, "GET", "/auth/me", headers=bearer("corrupt"))

    for response in (unavailable, corrupt):
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "access_store_unavailable"


async def test_status_reads_are_rate_limited_before_the_store_is_read():
    app, store = make_app(limiter=rate_limiter(overrides={"access_status": (2, 100)}))

    statuses = [
        (await call(app, "GET", "/auth/me", headers=bearer("listener"))).status_code
        for _ in range(4)
    ]

    assert statuses == [200, 200, 429, 429]
    assert store.calls.count("get") == 2


# ── /access/request: only the caller, only once ──────────────────────────


async def test_request_creates_one_pending_record_from_token_identity_only():
    app, store = make_app()

    first = await call(app, "POST", "/access/request", headers=bearer("listener"))
    second = await call(
        app, "POST", "/access/request", headers=bearer("listener"), payload={}
    )

    assert first.status_code == 200
    assert first.json()["access_status"] == "pending"
    assert first.json()["created"] is True
    assert second.json() == {**first.json(), "created": False}
    stored = store.users["listener"]
    assert stored["email"] == "listener@example.com"
    assert stored["revision"] == 1
    assert stored["decided_by"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"uid": "someone-else"},
        {"email": "admin@example.com"},
        {"status": "approved"},
        {"role": "admin"},
        {"decided_by": ADMIN},
        {"isAdmin": True},
    ],
)
async def test_request_body_cannot_choose_identity_status_or_role(payload):
    app, store = make_app()

    response = await call(
        app, "POST", "/access/request", headers=bearer("listener"), payload=payload
    )

    assert response.status_code == 422
    assert store.users == {}


@pytest.mark.parametrize("status", ["rejected", "blocked", "approved"])
async def test_repeated_request_never_reopens_or_overwrites_a_decision(status):
    app, store = make_app()
    store.seed("listener", status, revision=4)
    before = dict(store.users["listener"])

    response = await call(app, "POST", "/access/request", headers=bearer("listener"))

    assert response.status_code == 200
    assert response.json()["access_status"] == status
    assert response.json()["created"] is False
    assert store.users["listener"] == before


async def test_request_daily_quota_stops_new_records_but_not_status_replies():
    app, store = make_app(InMemoryAccessStore(admin_uids={ADMIN}, daily_limit=1))

    first = await call(app, "POST", "/access/request", headers=bearer("one"))
    blocked = await call(app, "POST", "/access/request", headers=bearer("two"))
    repeat = await call(app, "POST", "/access/request", headers=bearer("one"))

    assert first.status_code == 200
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "access_request_quota_exceeded"
    assert "two" not in store.users
    assert repeat.status_code == 200


async def test_request_rate_limit_outage_and_identity_failures():
    app, store = make_app(limiter=rate_limiter(overrides={"access_request": (1, 100)}))
    assert (
        await call(app, "POST", "/access/request", headers=bearer("a"))
    ).status_code == 200
    limited = await call(app, "POST", "/access/request", headers=bearer("a"))
    assert limited.status_code == 429
    assert limited.headers["Retry-After"]

    store.failure = outage()
    down = await call(app, "POST", "/access/request", headers=bearer("b"))
    assert down.status_code == 503
    assert (
        await call(
            app, "POST", "/access/request", headers={"Authorization": "Bearer invalid"}
        )
    ).status_code == 401
    assert (
        await call(
            app,
            "POST",
            "/access/request",
            headers={"Authorization": "Bearer unverified"},
        )
    ).status_code == 403


async def test_oversized_access_body_is_refused_before_parsing():
    app, store = make_app()

    response = await call(
        app,
        "POST",
        "/access/request",
        headers={**bearer("listener"), "Content-Type": "application/json"},
        content=json.dumps({"pad": "x" * 5_000}).encode(),
    )

    assert response.status_code == 413
    assert store.calls == []


# ── Administrator list ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path", ["/admin/access-users", "/admin/access-users?status=approved"]
)
async def test_non_admin_and_anonymous_callers_cannot_list(path):
    app, store = make_app()
    store.seed("listener", "approved")

    anonymous = await call(app, "GET", path)
    regular = await call(app, "GET", path, headers=bearer("listener"))
    spoofed = await call(
        app,
        "GET",
        path,
        headers={**bearer("listener"), "X-Side-B-Role": "admin", "isAdmin": "true"},
    )

    assert anonymous.status_code == 401
    assert regular.status_code == 403
    assert regular.json()["detail"]["code"] == "admin_required"
    assert spoofed.status_code == 403
    assert "list_users" not in store.calls


async def test_admin_list_paginates_by_status_with_bounded_pages():
    app, store = make_app()
    for index in range(30):
        store.seed(f"pending-{index:02d}", "pending")
    store.seed("approved-1", "approved")
    store.seed(ADMIN, "approved")

    first = await call(app, "GET", "/admin/access-users", headers=bearer(ADMIN))
    assert first.status_code == 200
    assert len(first.json()["items"]) == 25
    cursor = first.json()["next_cursor"]
    second = await call(
        app,
        "GET",
        f"/admin/access-users?status=pending&cursor={cursor}",
        headers=bearer(ADMIN),
    )
    seen = [item["uid"] for item in first.json()["items"] + second.json()["items"]]
    assert seen == [f"pending-{index:02d}" for index in range(30)]
    assert second.json()["next_cursor"] is None

    approved = await call(
        app,
        "GET",
        "/admin/access-users?status=approved&limit=50",
        headers=bearer(ADMIN),
    )
    flags = {item["uid"]: item["is_admin"] for item in approved.json()["items"]}
    assert flags == {"approved-1": False, ADMIN: True}

    wrong_filter = await call(
        app,
        "GET",
        f"/admin/access-users?status=approved&cursor={cursor}",
        headers=bearer(ADMIN),
    )
    assert wrong_filter.status_code == 422


@pytest.mark.parametrize(
    "query",
    [
        "limit=51",
        "limit=0",
        "limit=-1",
        "status=unregistered",
        "status=admin",
        "cursor=not-a-cursor",
        "cursor=" + "a" * 513,
    ],
)
async def test_admin_list_rejects_unbounded_or_invalid_queries(query):
    app, _store = make_app()

    response = await call(
        app, "GET", f"/admin/access-users?{query}", headers=bearer(ADMIN)
    )

    assert response.status_code == 422


# ── Administrator decisions ──────────────────────────────────────────────


async def test_approval_writes_state_and_one_token_free_audit_record():
    app, store = make_app()
    store.seed("listener", "pending")
    operation_id = str(uuid.uuid4())

    response = await call(
        app,
        "POST",
        "/admin/access-users/listener/decision",
        headers=bearer(ADMIN),
        payload=decision(operation_id=operation_id),
    )

    assert response.status_code == 200
    assert response.json() | {"decided_at": None} == {
        "uid": "listener",
        "action": "approve",
        "previous_status": "pending",
        "status": "approved",
        "revision": 2,
        "operation_id": operation_id,
        "decided_at": None,
        "replayed": False,
    }
    assert store.users["listener"]["decided_by"] == ADMIN
    assert list(store.audits) == [f"{ADMIN}:{operation_id}"]
    audit = store.audits[f"{ADMIN}:{operation_id}"]
    assert set(audit) == {
        "operation_id",
        "actor_uid",
        "target_uid",
        "action",
        "expected_revision",
        "previous_status",
        "new_status",
        "previous_revision",
        "new_revision",
        "created_at",
    }
    assert "user-" not in json.dumps(audit, default=str)


async def test_replay_returns_the_stored_result_without_a_second_audit():
    app, store = make_app()
    store.seed("listener", "pending")
    body = decision()
    path = "/admin/access-users/listener/decision"

    first = await call(app, "POST", path, headers=bearer(ADMIN), payload=body)
    replay = await call(app, "POST", path, headers=bearer(ADMIN), payload=body)

    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["status"] == first.json()["status"] == "approved"
    assert replay.json()["revision"] == 2
    assert len(store.audits) == 1
    assert store.users["listener"]["revision"] == 2


@pytest.mark.parametrize(
    "changed",
    [{"action": "reject"}, {"revision": 7}],
)
async def test_reusing_an_operation_id_for_different_work_is_a_conflict(changed):
    app, store = make_app()
    store.seed("listener", "pending")
    operation_id = str(uuid.uuid4())
    path = "/admin/access-users/listener/decision"
    await call(
        app,
        "POST",
        path,
        headers=bearer(ADMIN),
        payload=decision(operation_id=operation_id),
    )

    response = await call(
        app,
        "POST",
        path,
        headers=bearer(ADMIN),
        payload=decision(operation_id=operation_id, **changed),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "access_operation_conflict"
    assert len(store.audits) == 1


async def test_operation_id_reuse_on_another_target_is_a_conflict():
    app, store = make_app()
    store.seed("a", "pending")
    store.seed("b", "pending")
    operation_id = str(uuid.uuid4())
    await call(
        app,
        "POST",
        "/admin/access-users/a/decision",
        headers=bearer(ADMIN),
        payload=decision(operation_id=operation_id),
    )

    response = await call(
        app,
        "POST",
        "/admin/access-users/b/decision",
        headers=bearer(ADMIN),
        payload=decision(operation_id=operation_id),
    )

    assert response.status_code == 409
    assert store.users["b"]["status"] == "pending"


async def test_replay_rechecks_administrator_rights_first():
    store = InMemoryAccessStore(admin_uids={ADMIN, "second-admin"})
    store.seed("listener", "pending")
    app, _ = make_app(store, admin_uids=(ADMIN, "second-admin"))
    body = decision()
    path = "/admin/access-users/listener/decision"
    assert (
        await call(app, "POST", path, headers=bearer("second-admin"), payload=body)
    ).status_code == 200

    app.state.admin_uids = frozenset({ADMIN})  # operator removed the second admin
    replay = await call(app, "POST", path, headers=bearer("second-admin"), payload=body)

    assert replay.status_code == 403
    assert replay.json()["detail"]["code"] == "admin_required"
    assert store.calls.count("decide") == 1


async def test_stale_revision_and_invalid_transitions_return_current_state():
    app, store = make_app()
    store.seed("listener", "approved", revision=3)
    path = "/admin/access-users/listener/decision"

    stale = await call(
        app, "POST", path, headers=bearer(ADMIN), payload=decision("block", revision=2)
    )
    invalid = await call(
        app,
        "POST",
        path,
        headers=bearer(ADMIN),
        payload=decision("approve", revision=3),
    )

    assert stale.status_code == 409
    assert stale.json()["detail"] | {"message": None} == {
        "code": "access_revision_conflict",
        "message": None,
        "current_status": "approved",
        "current_revision": 3,
    }
    assert invalid.status_code == 409
    assert invalid.json()["detail"]["code"] == "access_invalid_transition"
    assert store.users["listener"]["revision"] == 3
    assert store.audits == {}


async def test_full_state_machine_and_no_self_service_reopen():
    app, store = make_app()
    path = "/admin/access-users/listener/decision"
    me = bearer("listener")

    async def decide(action, revision):
        response = await call(
            app, "POST", path, headers=bearer(ADMIN), payload=decision(action, revision)
        )
        assert response.status_code == 200, response.text
        return response.json()

    await call(app, "POST", "/access/request", headers=me)
    assert (await decide("reject", 1))["status"] == "rejected"
    again = await call(app, "POST", "/access/request", headers=me)
    assert again.json()["access_status"] == "rejected"
    assert (await decide("reopen", 2))["status"] == "pending"
    assert (await decide("approve", 3))["status"] == "approved"
    assert (await decide("block", 4))["status"] == "blocked"
    again = await call(app, "POST", "/access/request", headers=me)
    assert again.json()["access_status"] == "blocked"
    assert (await decide("unblock", 5))["status"] == "approved"
    assert len(store.audits) == 5


async def test_concurrent_decisions_on_one_revision_allow_exactly_one_winner():
    app, store = make_app()
    store.seed("listener", "pending")
    path = "/admin/access-users/listener/decision"

    responses = await asyncio.gather(
        call(app, "POST", path, headers=bearer(ADMIN), payload=decision("approve")),
        call(app, "POST", path, headers=bearer(ADMIN), payload=decision("reject")),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(store.audits) == 1
    assert store.users["listener"]["revision"] == 2


@pytest.mark.parametrize(
    "extra",
    [
        {"role": "admin"},
        {"email": "other@example.com"},
        {"decided_by": "attacker"},
        {"status": "approved"},
        {"actor_uid": ADMIN},
    ],
)
async def test_decision_body_cannot_inject_role_identity_or_decider(extra):
    app, store = make_app()
    store.seed("listener", "pending")

    response = await call(
        app,
        "POST",
        "/admin/access-users/listener/decision",
        headers=bearer(ADMIN),
        payload=decision(**extra),
    )

    assert response.status_code == 422
    assert store.users["listener"]["status"] == "pending"


@pytest.mark.parametrize(
    "payload",
    [
        decision(action="make_admin"),
        decision(action="APPROVE"),
        decision(operation_id="not-a-uuid"),
        decision(operation_id="../../x"),
        decision(revision="1"),
        decision(revision=True),
        decision(revision=-1),
        decision(revision=1.5),
        {"action": "approve", "operation_id": str(uuid.uuid4())},
    ],
)
async def test_decision_requires_allowlisted_action_uuid_and_integer_revision(payload):
    app, store = make_app()
    store.seed("listener", "pending")

    response = await call(
        app,
        "POST",
        "/admin/access-users/listener/decision",
        headers=bearer(ADMIN),
        payload=payload,
    )

    assert response.status_code == 422
    assert "decide" not in store.calls


@pytest.mark.parametrize(
    "target",
    ["a/b", "a%2Fb", "", "x" * 129, "__reserved__", "%00x"],
)
async def test_decision_path_uid_is_validated_as_a_document_id(target):
    app, store = make_app()

    response = await call(
        app,
        "POST",
        f"/admin/access-users/{target}/decision",
        headers=bearer(ADMIN),
        payload=decision(),
    )

    assert response.status_code == 422
    assert "decide" not in store.calls


async def test_non_ascii_uid_is_accepted_as_a_target():
    app, store = make_app()
    store.seed("사용자-1", "pending")

    response = await call(
        app,
        "POST",
        "/admin/access-users/%EC%82%AC%EC%9A%A9%EC%9E%90-1/decision",
        headers=bearer(ADMIN),
        payload=decision(),
    )

    assert response.status_code == 200
    assert store.users["사용자-1"]["status"] == "approved"


async def test_regular_users_cannot_decide_even_for_themselves():
    app, store = make_app()
    store.seed("listener", "pending")

    response = await call(
        app,
        "POST",
        "/admin/access-users/listener/decision",
        headers=bearer("listener"),
        payload=decision(role="admin"),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "admin_required"
    assert store.users["listener"]["status"] == "pending"


async def test_administrator_targets_cannot_be_changed_through_the_api():
    app, store = make_app(admin_uids=(ADMIN, "other-admin"))
    store.seed("other-admin", "approved")

    response = await call(
        app,
        "POST",
        "/admin/access-users/other-admin/decision",
        headers=bearer(ADMIN),
        payload=decision("block"),
    )
    own = await call(
        app,
        "POST",
        f"/admin/access-users/{ADMIN}/decision",
        headers=bearer(ADMIN),
        payload=decision("block"),
    )

    for result in (response, own):
        assert result.status_code == 403
        assert result.json()["detail"]["code"] == "access_admin_target_protected"
    assert store.audits == {}


async def test_missing_target_store_outage_and_rate_limits():
    app, store = make_app(limiter=rate_limiter(overrides={"admin_write": (2, 100)}))
    path = "/admin/access-users/ghost/decision"

    missing = await call(app, "POST", path, headers=bearer(ADMIN), payload=decision())
    store.failure = outage()
    down = await call(app, "POST", path, headers=bearer(ADMIN), payload=decision())
    limited = await call(app, "POST", path, headers=bearer(ADMIN), payload=decision())

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "access_user_not_found"
    assert down.status_code == 503
    assert down.json()["detail"]["code"] == "access_store_unavailable"
    assert limited.status_code == 429


async def test_env_mode_has_no_management_endpoints():
    app, _store = make_app()
    app.state.access_store = None

    for method, path in [
        ("POST", "/access/request"),
        ("GET", "/admin/access-users"),
        ("POST", "/admin/access-users/x/decision"),
    ]:
        payload = decision() if path.endswith("decision") else None
        response = await call(app, method, path, headers=bearer(ADMIN), payload=payload)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "access_management_disabled"


async def test_store_with_non_firebase_identity_fails_closed():
    app, store = make_app()
    app.state.auth_service = AuthenticationService(
        mode="dual",
        legacy_token="legacy",
        firebase_verifier=FirebaseTokenVerifier(PROJECT_ID, verify_transport=transport),
    )

    responses = [
        await call(app, "GET", "/auth/me", headers={"X-Side-B-Access-Token": "legacy"}),
        await call(
            app,
            "POST",
            "/recommend",
            payload={"query": "x"},
            headers={"X-Side-B-Access-Token": "legacy"},
        ),
    ]

    for response in responses:
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "auth_configuration_error"
    assert store.calls == []


# ── Features require approval in Firestore mode ──────────────────────────


@pytest.mark.parametrize("status", [None, "pending", "rejected", "blocked"])
@pytest.mark.parametrize(("method", "path", "payload", "handler"), FEATURE_REQUESTS)
async def test_every_feature_and_preview_requires_an_approved_account(
    feature_handlers, status, method, path, payload, handler
):
    app, store = make_app(handlers=feature_handlers)
    if status:
        store.seed("listener", status)

    response = await call(
        app, method, path, headers=bearer("listener"), payload=payload
    )

    assert response.status_code == 403
    assert response.json()["detail"] | {"message": None} == {
        "code": "access_not_approved",
        "message": None,
        "access_status": status or "unregistered",
    }
    assert feature_handlers.calls == []


@pytest.mark.parametrize(("method", "path", "payload", "handler"), FEATURE_REQUESTS)
async def test_approved_accounts_reach_every_feature(
    feature_handlers, method, path, payload, handler
):
    app, store = make_app(handlers=feature_handlers)
    store.seed("listener", "approved")

    response = await call(
        app, method, path, headers=bearer("listener"), payload=payload
    )

    assert response.status_code == 200, response.text
    assert handler in feature_handlers.calls


@pytest.mark.parametrize(("method", "path", "payload", "handler"), FEATURE_REQUESTS)
async def test_features_and_preview_need_a_bearer_token_in_firestore_mode(
    feature_handlers, method, path, payload, handler
):
    app, store = make_app(handlers=feature_handlers)
    store.seed("listener", "approved")

    anonymous = await call(app, method, path, payload=payload)
    legacy = await call(
        app,
        method,
        path,
        payload=payload,
        headers={"X-Side-B-Access-Token": "legacy", "X-Side-B-Export-Token": "legacy"},
    )

    assert anonymous.status_code == legacy.status_code == 401
    assert feature_handlers.calls == []


async def test_block_takes_effect_on_the_next_feature_request(feature_handlers):
    app, store = make_app(handlers=feature_handlers)
    store.seed("listener", "approved")
    path = "/admin/access-users/listener/decision"
    first = await call(
        app, "POST", "/recommend", headers=bearer("listener"), payload={"query": "x"}
    )
    block = await call(
        app, "POST", path, headers=bearer(ADMIN), payload=decision("block")
    )
    second = await call(
        app, "POST", "/recommend", headers=bearer("listener"), payload={"query": "x"}
    )

    assert first.status_code == 200
    assert block.status_code == 200
    assert second.status_code == 403
    assert second.json()["detail"]["access_status"] == "blocked"
    assert feature_handlers.calls == ["recommend"]


@pytest.mark.parametrize(("method", "path", "payload", "handler"), FEATURE_REQUESTS)
async def test_store_outage_fails_features_closed_without_running_them(
    feature_handlers, method, path, payload, handler
):
    app, store = make_app(handlers=feature_handlers)
    store.seed("listener", "approved")
    store.failure = outage()

    response = await call(
        app, method, path, headers=bearer("listener"), payload=payload
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "access_store_unavailable"
    assert feature_handlers.calls == []


async def test_unapproved_accounts_do_not_drain_feature_budgets(feature_handlers):
    limiter = rate_limiter(overrides={"recommend": (5, 1)})
    app, store = make_app(limiter=limiter, handlers=feature_handlers)
    store.seed("pending", "pending")
    store.seed("approved", "approved")
    for _ in range(3):
        assert (
            await call(
                app,
                "POST",
                "/recommend",
                headers=bearer("pending"),
                payload={"query": "x"},
            )
        ).status_code == 403

    response = await call(
        app, "POST", "/recommend", headers=bearer("approved"), payload={"query": "x"}
    )

    assert response.status_code == 200


async def test_store_reads_per_account_are_bounded_by_the_lookup_budget(
    feature_handlers,
):
    app, store = make_app(
        limiter=rate_limiter(overrides={"access_lookup": (2, 100)}),
        handlers=feature_handlers,
    )
    store.seed("pending", "pending")

    statuses = [
        (
            await call(
                app,
                "POST",
                "/recommend",
                headers=bearer("pending"),
                payload={"query": "x"},
            )
        ).status_code
        for _ in range(4)
    ]

    assert statuses == [403, 403, 429, 429]
    assert store.calls.count("get") == 2


# ── Startup wiring ───────────────────────────────────────────────────────


def firestore_settings(**overrides):
    values = {
        "SIDE_B_ACCESS_STORE": "firestore",
        "SIDE_B_AUTH_MODE": "firebase",
        "FIREBASE_PROJECT_ID": PROJECT_ID,
        "SIDE_B_ADMIN_UIDS": ADMIN,
        "SIDE_B_ACCESS_TOKEN": "",
        "FIREBASE_ALLOWED_UIDS": "",
        "FIREBASE_ALLOWED_EMAILS": "",
        "ALLOW_UNAUTHENTICATED_RECOMMEND": "false",
        **overrides,
    }
    return Settings(_env_file=None, **{key: value for key, value in values.items()})


@pytest.mark.parametrize(
    "overrides",
    [
        {"SIDE_B_AUTH_MODE": "dual"},
        {"SIDE_B_AUTH_MODE": "legacy"},
        {"SIDE_B_ADMIN_UIDS": ""},
        {"SIDE_B_ADMIN_UIDS": " , "},
        {"SIDE_B_ADMIN_UIDS": "good,bad/uid"},
        {"FIREBASE_PROJECT_ID": ""},
        {"ALLOW_UNAUTHENTICATED_RECOMMEND": "true"},
    ],
)
async def test_unsafe_firestore_configuration_stops_startup(monkeypatch, overrides):
    settings = firestore_settings(**overrides)
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    with pytest.raises(AccessConfigurationError):
        async with main.lifespan(FastAPI()):
            pass


async def test_env_allowlist_and_legacy_token_never_bypass_firestore(
    feature_handlers, caplog
):
    settings = firestore_settings(
        FIREBASE_ALLOWED_UIDS="listener",
        FIREBASE_ALLOWED_EMAILS="listener@example.com",
        SIDE_B_ACCESS_TOKEN="legacy",
    )
    app, _ = make_app(handlers=feature_handlers)
    main.configure_access(app, settings)
    app.state.auth_service._firebase_verifier._verify_transport = transport
    store = InMemoryAccessStore(admin_uids={ADMIN})
    app.state.access_store = store

    assert app.state.auth_service.mode == "firebase"
    assert app.state.auth_service.enforce_allowlist is False
    assert app.state.admin_uids == frozenset({ADMIN})
    assert "ignored" in caplog.text
    allowlisted = await call(
        app, "POST", "/recommend", headers=bearer("listener"), payload={"query": "x"}
    )
    shared = await call(
        app,
        "POST",
        "/recommend",
        headers={"X-Side-B-Access-Token": "legacy"},
        payload={"query": "x"},
    )
    store.failure = outage()
    outage_response = await call(
        app, "POST", "/recommend", headers=bearer("listener"), payload={"query": "x"}
    )

    assert allowlisted.status_code == 403
    assert allowlisted.json()["detail"]["access_status"] == "unregistered"
    assert shared.status_code == 401
    assert outage_response.status_code == 503
    assert feature_handlers.calls == []


def test_external_approval_cannot_be_constructed_with_fallbacks():
    verifier = FirebaseTokenVerifier(PROJECT_ID, verify_transport=transport)
    for kwargs in [
        {"mode": "dual"},
        {"mode": "legacy"},
        {"mode": "firebase", "allowed_uids": frozenset({"x"})},
        {"mode": "firebase", "allowed_emails": frozenset({"x@example.com"})},
        {
            "mode": "firebase",
            "unauthenticated_legacy_features": frozenset({"recommend"}),
        },
    ]:
        with pytest.raises(ValueError):
            AuthenticationService(
                legacy_token=None,
                firebase_verifier=verifier,
                enforce_allowlist=False,
                **kwargs,
            )


async def test_env_mode_preview_stays_public_and_unchanged(feature_handlers):
    app, _store = make_app(handlers=feature_handlers)
    app.state.access_store = None
    app.state.auth_service = AuthenticationService(
        mode="dual",
        legacy_token="legacy",
        firebase_verifier=FirebaseTokenVerifier(PROJECT_ID, verify_transport=transport),
        allowed_uids=frozenset({"listener"}),
    )

    response = await call(app, "GET", "/preview?track=Seed&artist=Artist")

    assert response.status_code == 200
    assert feature_handlers.calls == ["preview"]
