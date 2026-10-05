"""Bootstrap migration script logic with fake Firebase Auth and Firestore.

The script is not executed against any real project in these tests.
"""

import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.access import AUDIT_COLLECTION, USERS_COLLECTION
from scripts import bootstrap_access
from tests.firestore_fakes import FakeFirestore, fake_transactional

NOW = datetime(2026, 10, 5, tzinfo=UTC)


class FakeAuth:
    def __init__(self, users):
        self.users = {user.uid: user for user in users}

    def get_user(self, uid):
        if uid not in self.users:
            raise LookupError("missing")
        return self.users[uid]

    def get_user_by_email(self, email):
        for user in self.users.values():
            if user.email == email:
                return user
        raise LookupError("missing")


def user(uid, *, provider="google.com", verified=True, disabled=False):
    return SimpleNamespace(
        uid=uid,
        email=f"{uid}@example.com",
        display_name=uid.title(),
        email_verified=verified,
        disabled=disabled,
        provider_data=[SimpleNamespace(provider_id=provider)],
    )


def run(argv, auth, db):
    out = io.StringIO()
    code = bootstrap_access.main(
        argv,
        auth_api=auth,
        db_factory=lambda project, database: (db, fake_transactional),
        now=NOW,
        out=out,
    )
    return code, json.loads(out.getvalue())


def test_dry_run_is_the_default_and_writes_nothing():
    db = FakeFirestore()
    auth = FakeAuth([user("admin"), user("member")])

    code, report = run(
        [
            "--project",
            "p",
            "--admin-uid",
            "admin",
            "--approved-email",
            "member@example.com",
        ],
        auth,
        db,
    )

    assert code == 0
    assert report["applied"] is False
    assert [entry["result"] for entry in report["accounts"]] == ["would_create"] * 2
    assert db.data == {}


def test_apply_creates_approved_documents_and_audits_without_overwriting():
    db = FakeFirestore()
    db.write_now(f"{USERS_COLLECTION}/blocked", {"status": "blocked", "revision": 3})
    auth = FakeAuth([user("admin"), user("member"), user("blocked")])

    code, report = run(
        [
            "--project",
            "p",
            "--admin-uid",
            "admin",
            "--approved-uid",
            "member",
            "--approved-uid",
            "blocked",
            "--apply",
        ],
        auth,
        db,
    )

    assert code == 0
    results = {entry["uid"]: entry for entry in report["accounts"]}
    assert results["admin"]["role"] == "admin"
    assert results["admin"]["result"] == "created"
    assert results["member"]["result"] == "created"
    assert results["blocked"] == {
        "uid": "blocked",
        "role": "user",
        "result": "exists",
        "status": "blocked",
    }
    assert db.data[f"{USERS_COLLECTION}/blocked"]["status"] == "blocked"
    member = db.data[f"{USERS_COLLECTION}/member"]
    assert member["status"] == "approved" and member["revision"] == 1
    assert member["email"] == "member@example.com"
    assert len([path for path in db.data if path.startswith(AUDIT_COLLECTION)]) == 2
    assert "token" not in json.dumps(report).casefold()


@pytest.mark.parametrize(
    ("accounts", "argv"),
    [
        ([user("admin", provider="password")], ["--admin-uid", "admin"]),
        ([user("admin", verified=False)], ["--admin-uid", "admin"]),
        ([user("admin", disabled=True)], ["--admin-uid", "admin"]),
        ([user("admin")], ["--admin-uid", "admin", "--approved-uid", "ghost"]),
        ([user("admin")], ["--admin-uid", "admin", "--approved-uid", "bad/uid"]),
        ([user("member")], ["--approved-uid", "member"]),
    ],
)
def test_unverifiable_inputs_abort_before_any_write(accounts, argv):
    db = FakeFirestore()

    code, report = run(["--project", "p", *argv, "--apply"], FakeAuth(accounts), db)

    assert code == 2
    assert report["ok"] is False
    assert db.data == {}
