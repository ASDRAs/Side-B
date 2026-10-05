import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.services.access import (
    TRANSITIONS,
    AccessConflictError,
    AccessIdentity,
    AccessRecordInvalidError,
    AccessRequestQuotaExceededError,
    AccessTargetProtectedError,
    AccessUserNotFoundError,
    DecisionRequest,
    InvalidAccessInputError,
    ListCursor,
    audit_document_id,
    decode_cursor,
    encode_cursor,
    plan_access_request,
    plan_decision,
    record_from_data,
    validate_operation_id,
    validate_uid,
)

NOW = datetime(2026, 10, 5, 3, 4, 5, 123456, tzinfo=UTC)
OP = "6f1d2b8e-3c4a-4e5f-9a7b-1c2d3e4f5a6b"


def user(status, revision=1):
    return {"status": status, "revision": revision, "requested_at": NOW}


def request(action="approve", revision=1, target="target", actor="admin", op=OP):
    return DecisionRequest(
        actor_uid=actor,
        target_uid=target,
        action=action,
        expected_revision=revision,
        operation_id=op,
    )


@pytest.mark.parametrize(
    "uid",
    [
        "a",
        "x" * 128,
        "사용자-uid",
        "user.with.dots",
        "with space",
        "under_score__",
        "__x",
    ],
)
def test_uid_accepts_firebase_ids_without_an_ascii_allowlist(uid):
    assert validate_uid(uid) == uid


@pytest.mark.parametrize(
    "uid",
    [
        "",
        "x" * 129,
        "a/b",
        "/",
        ".",
        "..",
        "__name__",
        "__x__",
        "tab\tuid",
        "nul\x00",
        "\ud800",
        None,
        7,
    ],
)
def test_uid_rejects_document_path_hazards(uid):
    with pytest.raises(InvalidAccessInputError):
        validate_uid(uid)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-uuid",
        OP.upper() + "0",
        "{" + OP + "}",
        "6f1d2b8e3c4a4e5f9a7b1c2d3e4f5a6b",
        None,
    ],
)
def test_operation_id_must_be_a_canonical_uuid(value):
    with pytest.raises(InvalidAccessInputError):
        validate_operation_id(value)


def test_audit_key_is_scoped_to_the_requesting_administrator():
    assert audit_document_id("admin-a", OP) == f"admin-a:{OP}"
    assert audit_document_id("admin-a", OP) != audit_document_id("admin-b", OP)
    assert validate_operation_id(OP.upper()) == OP


@pytest.mark.parametrize(("action", "transition"), sorted(TRANSITIONS.items()))
def test_each_allowlisted_transition_bumps_revision_and_records_audit(
    action, transition
):
    source, target = transition

    plan = plan_decision(
        request(action, revision=4),
        user=user(source, revision=4),
        audit=None,
        admin_uids=frozenset({"admin"}),
        now=NOW,
    )

    assert plan.result.status == target
    assert plan.result.previous_status == source
    assert plan.result.revision == 5
    assert plan.user_update == {
        "status": target,
        "revision": 5,
        "updated_at": NOW,
        "decided_at": NOW,
        "decided_by": "admin",
    }
    assert plan.audit_data["previous_status"] == source
    assert plan.audit_data["new_status"] == target
    assert plan.audit_data["actor_uid"] == "admin"


@pytest.mark.parametrize(
    ("action", "status"),
    [
        (action, status)
        for action in sorted(TRANSITIONS)
        for status in ("pending", "approved", "rejected", "blocked")
        if TRANSITIONS[action][0] != status
    ],
)
def test_every_other_combination_is_an_invalid_transition(action, status):
    with pytest.raises(AccessConflictError) as error:
        plan_decision(
            request(action),
            user=user(status),
            audit=None,
            admin_uids=frozenset(),
            now=NOW,
        )
    assert error.value.code == "access_invalid_transition"
    assert error.value.current_status == status


def test_unknown_actions_cannot_reach_the_store():
    with pytest.raises(InvalidAccessInputError):
        plan_decision(
            request("grant_admin"),
            user=user("pending"),
            audit=None,
            admin_uids=frozenset(),
            now=NOW,
        )


def test_revision_mismatch_is_checked_before_the_transition():
    with pytest.raises(AccessConflictError) as error:
        plan_decision(
            request("approve", revision=1),
            user=user("approved", revision=2),
            audit=None,
            admin_uids=frozenset(),
            now=NOW,
        )
    assert error.value.code == "access_revision_conflict"
    assert error.value.current_revision == 2


def test_missing_targets_and_admin_targets_are_refused():
    with pytest.raises(AccessUserNotFoundError):
        plan_decision(request(), user=None, audit=None, admin_uids=frozenset(), now=NOW)
    with pytest.raises(AccessTargetProtectedError):
        plan_decision(
            request(target="admin"),
            user=user("pending"),
            audit=None,
            admin_uids=frozenset({"admin"}),
            now=NOW,
        )


def test_replay_needs_identical_actor_target_action_and_revision():
    first = plan_decision(
        request(), user=user("pending"), audit=None, admin_uids=frozenset(), now=NOW
    )
    audit = first.audit_data

    replay = plan_decision(
        request(),
        user=user("approved", revision=2),
        audit=audit,
        admin_uids=frozenset(),
        now=NOW + timedelta(hours=1),
    )

    assert replay.result.replayed is True
    assert replay.user_update is None and replay.audit_data is None
    assert replay.result.status == "approved"
    assert replay.result.decided_at == NOW
    for changed in (
        request(action="reject"),
        request(revision=2),
        request(target="other"),
    ):
        with pytest.raises(AccessConflictError) as error:
            plan_decision(
                changed,
                user=user("pending"),
                audit=audit,
                admin_uids=frozenset(),
                now=NOW,
            )
        assert error.value.code == "access_operation_conflict"


def test_audit_record_has_no_credentials_or_client_supplied_fields():
    plan = plan_decision(
        request(), user=user("pending"), audit=None, admin_uids=frozenset(), now=NOW
    )

    assert set(plan.audit_data) == {
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


def test_access_request_is_created_once_and_counted_against_the_daily_quota():
    identity = AccessIdentity(
        uid="listener", email=" Listener@Example.com ", display_name="L"
    )

    created = plan_access_request(
        identity, existing=None, quota={"count": 2}, daily_limit=3, now=NOW
    )
    repeated = plan_access_request(
        identity,
        existing=user("blocked", revision=9),
        quota=None,
        daily_limit=3,
        now=NOW,
    )

    assert created.result.created is True
    assert created.user_data["status"] == "pending"
    assert created.user_data["email"] == "Listener@Example.com"
    assert created.quota_data["count"] == 3
    assert repeated.result.created is False
    assert repeated.result.record.status == "blocked"
    assert repeated.user_data is None and repeated.quota_data is None
    with pytest.raises(AccessRequestQuotaExceededError):
        plan_access_request(
            identity, existing=None, quota={"count": 3}, daily_limit=3, now=NOW
        )


@pytest.mark.parametrize(
    "data",
    [
        {"status": "Approved", "revision": 1},
        {"status": "admin", "revision": 1},
        {"status": "approved", "revision": "1"},
        {"status": "approved", "revision": True},
        {"status": "approved", "revision": -1},
        {"status": "approved"},
        {"status": "approved", "revision": 1, "requested_at": "2026-10-05"},
    ],
)
def test_corrupt_records_are_store_errors_not_approvals(data):
    with pytest.raises(AccessRecordInvalidError):
        record_from_data("uid", data)


def test_cursor_round_trips_and_rejects_tampering():
    cursor = ListCursor(status="pending", requested_at=NOW, uid="사용자")
    token = encode_cursor(cursor)

    assert decode_cursor(token, "pending") == cursor
    for bad in [token + "!", "e30", "a" * 513, token[:-3]]:
        with pytest.raises(InvalidAccessInputError):
            decode_cursor(bad, "pending")
    with pytest.raises(InvalidAccessInputError):
        decode_cursor(token, "approved")


def test_corrupt_audit_record_fails_closed_on_replay():
    with pytest.raises(AccessRecordInvalidError):
        plan_decision(
            request(),
            user=user("approved", 2),
            audit={
                "actor_uid": "admin",
                "target_uid": "target",
                "action": "approve",
                "expected_revision": 1,
                "previous_status": "pending",
                "new_status": "superuser",
                "new_revision": 2,
            },
            admin_uids=frozenset(),
            now=NOW,
        )


def test_generated_operation_ids_are_accepted():
    for _ in range(20):
        value = str(uuid.uuid4())
        assert validate_operation_id(value) == value
