"""Real Firestore transaction and rules checks against the Firestore emulator.

Skipped unless ``FIRESTORE_EMULATOR_HOST`` is set. Start an emulator first, e.g.

    cd deployment/firestore
    firebase emulators:start --only firestore --project demo-side-b-access

then, in another shell from ``backend``:

    $env:FIRESTORE_EMULATOR_HOST = "127.0.0.1:8085"
    poetry run pytest tests/integration -q

A skipped run means these behaviors were NOT verified.
"""

import asyncio
import base64
import json
import os
import uuid
from pathlib import Path

import httpx
import pytest

from app.services.access import (
    AUDIT_COLLECTION,
    USERS_COLLECTION,
    AccessConflictError,
    AccessIdentity,
    AccessRequestQuotaExceededError,
    DecisionRequest,
    FirestoreAccessStore,
)

EMULATOR = os.environ.get("FIRESTORE_EMULATOR_HOST", "").strip()
PROJECT = os.environ.get("SIDE_B_EMULATOR_PROJECT", "demo-side-b-access")
RULES = Path(__file__).resolve().parents[3] / "deployment/firestore/firestore.rules"
DOCUMENTS = f"projects/{PROJECT}/databases/(default)/documents"

pytestmark = pytest.mark.skipif(
    not EMULATOR,
    reason="FIRESTORE_EMULATOR_HOST is not set: Firestore emulator checks were not run",
)


def _reset():
    response = httpx.delete(f"http://{EMULATOR}/emulator/v1/{DOCUMENTS}", timeout=10)
    response.raise_for_status()


@pytest.fixture(autouse=True)
def clean_database():
    _reset()
    yield
    _reset()


def make_store(**kwargs):
    from google.cloud import firestore

    client = firestore.Client(project=PROJECT)
    options = {
        "project_id": PROJECT,
        "database": "(default)",
        "admin_uids": frozenset({"admin"}),
        "request_daily_limit": 50,
        "max_concurrency": 16,
        "transaction_attempts": 10,
        "client_factory": lambda: client,
    }
    options.update(kwargs)
    return FirestoreAccessStore(**options), client


def decision(action="approve", revision=1, op=None):
    return DecisionRequest(
        actor_uid="admin",
        target_uid="listener",
        action=action,
        expected_revision=revision,
        operation_id=op or str(uuid.uuid4()),
    )


def audit_count(client):
    return len(list(client.collection(AUDIT_COLLECTION).stream()))


async def test_request_then_decision_writes_state_and_single_audit():
    store, client = make_store()
    created = await store.request_access(
        AccessIdentity(uid="listener", email="l@example.com")
    )
    assert created.created and created.record.status == "pending"

    request = decision()
    result = await store.decide(request)
    replay = await store.decide(request)

    assert result.status == "approved" and result.revision == 2
    assert replay.replayed is True
    assert audit_count(client) == 1
    stored = client.collection(USERS_COLLECTION).document("listener").get().to_dict()
    assert stored["status"] == "approved" and stored["decided_by"] == "admin"


async def test_parallel_decisions_on_one_revision_have_exactly_one_winner():
    store, client = make_store()
    await store.request_access(AccessIdentity(uid="listener"))

    results = await asyncio.gather(
        *(store.decide(decision("approve" if i % 2 else "reject")) for i in range(8)),
        return_exceptions=True,
    )

    winners = [result for result in results if not isinstance(result, Exception)]
    conflicts = [
        result for result in results if isinstance(result, AccessConflictError)
    ]
    assert len(winners) == 1
    assert len(conflicts) == 7
    assert audit_count(client) == 1


async def test_parallel_replays_of_one_operation_write_one_audit():
    store, client = make_store()
    await store.request_access(AccessIdentity(uid="listener"))
    request = decision()

    results = await asyncio.gather(*(store.decide(request) for _ in range(6)))

    assert sum(not result.replayed for result in results) == 1
    assert audit_count(client) == 1


async def test_parallel_requests_create_once_and_respect_the_daily_quota():
    store, client = make_store(request_daily_limit=3)

    same = await asyncio.gather(
        *(store.request_access(AccessIdentity(uid="same")) for _ in range(5))
    )
    others = await asyncio.gather(
        *(store.request_access(AccessIdentity(uid=f"user-{i}")) for i in range(6)),
        return_exceptions=True,
    )

    assert sum(result.created for result in same) == 1
    assert sum(not isinstance(result, Exception) for result in others) == 2
    assert all(
        isinstance(result, AccessRequestQuotaExceededError)
        for result in others
        if isinstance(result, Exception)
    )
    assert len(list(client.collection(USERS_COLLECTION).stream())) == 3


async def test_status_filtered_pagination_uses_a_stable_cursor():
    store, _client = make_store()
    for index in range(7):
        await store.request_access(AccessIdentity(uid=f"p{index}"))

    seen = []
    cursor = None
    while True:
        page = await store.list_users("pending", 3, cursor)
        seen.extend(record.uid for record in page.records)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert seen == [f"p{index}" for index in range(7)]


def _unsigned_token(uid):
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    header = part({"alg": "none", "typ": "JWT"})
    payload = part(
        {
            "sub": uid,
            "user_id": uid,
            "aud": PROJECT,
            "iss": f"https://securetoken.google.com/{PROJECT}",
            "email_verified": True,
            "firebase": {"sign_in_provider": "google.com"},
        }
    )
    return f"{header}.{payload}."


def test_security_rules_deny_every_direct_client_read_and_write():
    loaded = httpx.put(
        f"http://{EMULATOR}/emulator/v1/projects/{PROJECT}:securityRules",
        json={
            "rules": {
                "files": [{"name": "firestore.rules", "content": RULES.read_text()}]
            }
        },
        timeout=10,
    )
    loaded.raise_for_status()
    base = f"http://{EMULATOR}/v1/{DOCUMENTS}"
    owner = {"Authorization": "Bearer owner"}
    httpx.patch(
        f"{base}/{USERS_COLLECTION}/listener",
        json={"fields": {"status": {"stringValue": "pending"}}},
        headers=owner,
        timeout=10,
    ).raise_for_status()

    for headers in ({}, {"Authorization": f"Bearer {_unsigned_token('listener')}"}):
        read = httpx.get(
            f"{base}/{USERS_COLLECTION}/listener", headers=headers, timeout=10
        )
        write = httpx.patch(
            f"{base}/{USERS_COLLECTION}/listener",
            json={"fields": {"status": {"stringValue": "approved"}}},
            headers=headers,
            timeout=10,
        )
        audit = httpx.get(f"{base}/{AUDIT_COLLECTION}", headers=headers, timeout=10)
        assert read.status_code == 403
        assert write.status_code == 403
        assert audit.status_code == 403
