"""``FirestoreAccessStore`` glue code against ``tests/firestore_fakes.py``.

These check what the store asks Firestore to do (reads before writes, one
commit for state + audit, deterministic audit IDs, retry behavior, error and
timeout mapping). They do not prove real Firestore semantics; run
``tests/integration/test_firestore_emulator.py`` against an emulator for that.
"""

import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from google.api_core import exceptions as api_exceptions

from app.services.access import (
    AUDIT_COLLECTION,
    QUOTA_COLLECTION,
    USERS_COLLECTION,
    AccessConflictError,
    AccessIdentity,
    AccessRequestQuotaExceededError,
    AccessStoreUnavailableError,
    AccessTargetProtectedError,
    DecisionRequest,
    FirestoreAccessStore,
)
from tests.firestore_fakes import FakeFirestore, fake_transactional

START = datetime(2026, 10, 5, tzinfo=UTC)


def make_store(db=None, **kwargs):
    db = db or FakeFirestore()
    clock = [START]

    def tick():
        clock[0] += timedelta(seconds=1)
        return clock[0]

    options = {
        "project_id": "demo-side-b",
        "database": "(default)",
        "admin_uids": frozenset({"admin"}),
        "request_daily_limit": 50,
        "client_factory": lambda: db,
        "transactional": fake_transactional,
        "clock": tick,
        **kwargs,
    }
    return FirestoreAccessStore(**options), db


def seed(db, uid, status, revision=1, requested_at=START):
    db.write_now(
        f"{USERS_COLLECTION}/{uid}",
        {
            "uid": uid,
            "email": f"{uid}@example.com",
            "status": status,
            "revision": revision,
            "requested_at": requested_at,
            "updated_at": requested_at,
        },
    )


def decision(action="approve", revision=1, target="listener", op=None, actor="admin"):
    return DecisionRequest(
        actor_uid=actor,
        target_uid=target,
        action=action,
        expected_revision=revision,
        operation_id=op or str(uuid.uuid4()),
    )


def audits(db):
    return {
        path: data
        for path, data in db.data.items()
        if path.startswith(AUDIT_COLLECTION)
    }


def test_client_is_created_lazily_so_startup_needs_no_network():
    created = []
    store = FirestoreAccessStore(
        project_id="p",
        database="(default)",
        admin_uids=frozenset({"admin"}),
        request_daily_limit=1,
        client_factory=lambda: created.append(True),
    )
    assert store.database == "(default)"
    assert created == []


async def test_decision_commits_state_and_audit_together_once():
    store, db = make_store()
    seed(db, "listener", "pending")
    request = decision()

    result = await store.decide(request)

    assert result.status == "approved" and result.revision == 2
    assert len(db.commits) == 1
    assert [(kind, path.split("/")[0]) for kind, path, _ in db.commits[0]] == [
        ("update", USERS_COLLECTION),
        ("create", AUDIT_COLLECTION),
    ]
    assert list(audits(db)) == [f"{AUDIT_COLLECTION}/admin:{request.operation_id}"]


async def test_aborted_commit_retries_without_duplicate_audit_or_double_bump():
    store, db = make_store()
    seed(db, "listener", "pending")
    db.abort_commits = 2

    result = await store.decide(decision())

    assert db.attempts == 3
    assert len(db.commits) == 1
    assert len(audits(db)) == 1
    assert result.revision == 2
    assert db.data[f"{USERS_COLLECTION}/listener"]["revision"] == 2


@pytest.mark.parametrize("operation", ["request", "decision"])
async def test_aborted_transactional_read_retries_the_whole_transaction(operation):
    store, db = make_store()
    if operation == "decision":
        seed(db, "listener", "pending")
    reads = []

    def abort_read(ref, transaction):
        if transaction is not None and len(reads) < 2:
            reads.append(transaction)
            raise api_exceptions.Aborted("read contention")

    db.read_hook = abort_read
    if operation == "decision":
        result = await store.decide(decision())
        assert result.revision == 2 and len(audits(db)) == 1
    else:
        result = await store.request_access(AccessIdentity(uid="listener"))
        assert result.created is True
        assert db.data[f"{QUOTA_COLLECTION}/{START.date().isoformat()}"]["count"] == 1
    assert db.attempts == 3 and reads[0] is not reads[1]
    assert len(db.commits) == 1


async def test_concurrent_change_during_transaction_retries_into_a_conflict():
    store, db = make_store()
    seed(db, "listener", "pending")
    raced = []

    def other_admin_wins(transaction):
        # Another administrator commits after this transaction read the
        # document but before it commits.
        if not raced:
            raced.append(True)
            seed(db, "listener", "rejected", revision=2)

    db.commit_hook = other_admin_wins

    with pytest.raises(AccessConflictError) as error:
        await store.decide(decision("approve", revision=1))

    assert error.value.code == "access_revision_conflict"
    assert db.attempts == 2
    assert audits(db) == {}
    assert db.data[f"{USERS_COLLECTION}/listener"]["status"] == "rejected"


async def test_replay_reads_but_writes_nothing():
    store, db = make_store()
    seed(db, "listener", "pending")
    request = decision()
    await store.decide(request)

    replay = await store.decide(request)

    assert replay.replayed is True
    assert db.commits[-1] == []
    assert len(audits(db)) == 1


async def test_admin_targets_are_refused_before_any_database_access():
    store, db = make_store()
    db.read_hook = lambda *_: pytest.fail("must not read")

    with pytest.raises(AccessTargetProtectedError):
        await store.decide(decision(target="admin"))


async def test_exhausted_retries_and_sdk_errors_become_unavailable():
    store, db = make_store(transaction_attempts=2)
    seed(db, "listener", "pending")
    db.abort_commits = 5
    with pytest.raises(AccessStoreUnavailableError):
        await store.decide(decision())
    assert audits(db) == {}

    for error in (
        api_exceptions.ServiceUnavailable("down"),
        api_exceptions.PermissionDenied("iam"),
        api_exceptions.DeadlineExceeded("slow"),
        OSError("network"),
    ):

        def fail(*_args, error=error):
            raise error

        db.read_hook = fail
        with pytest.raises(AccessStoreUnavailableError):
            await store.get("listener")


async def test_request_creates_pending_and_counter_in_one_commit_then_never_rewrites():
    store, db = make_store()
    identity = AccessIdentity(uid="listener", email="l@example.com", display_name="L")

    first = await store.request_access(identity)
    second = await store.request_access(identity)

    assert first.created is True and second.created is False
    assert len(db.commits) == 2
    assert sorted(kind for kind, _path, _data in db.commits[0]) == ["create", "set"]
    assert db.commits[1] == []
    quota = [
        data for path, data in db.data.items() if path.startswith(QUOTA_COLLECTION)
    ]
    assert quota[0]["count"] == 1


async def test_request_quota_is_enforced_inside_the_transaction():
    store, db = make_store(request_daily_limit=1)
    await store.request_access(AccessIdentity(uid="one"))

    with pytest.raises(AccessRequestQuotaExceededError):
        await store.request_access(AccessIdentity(uid="two"))

    assert f"{USERS_COLLECTION}/two" not in db.data


async def test_list_query_filters_orders_and_pages_with_cursor():
    store, db = make_store()
    for index in range(5):
        seed(
            db,
            f"p{index}",
            "pending",
            requested_at=START + timedelta(minutes=5 - index),
        )
    seed(db, "a1", "approved")

    first = await store.list_users("pending", 2, None)
    second = await store.list_users("pending", 2, first.next_cursor)
    third = await store.list_users("pending", 2, second.next_cursor)

    uids = [record.uid for page in (first, second, third) for record in page.records]
    assert uids == ["p4", "p3", "p2", "p1", "p0"]
    assert third.next_cursor is None


async def test_slow_store_times_out_and_keeps_the_slot_until_the_call_finishes():
    release = threading.Event()
    store, db = make_store(operation_timeout_seconds=1.0, max_concurrency=1)
    store._timeout = 0.05
    seed(db, "listener", "approved")
    db.read_hook = lambda *_: release.wait(2)

    with pytest.raises(AccessStoreUnavailableError):
        await store.get("listener")
    with pytest.raises(AccessStoreUnavailableError):
        await store.get("listener")

    release.set()
    db.read_hook = None
    await asyncio.sleep(0.1)
    assert (await store.get("listener")).status == "approved"
