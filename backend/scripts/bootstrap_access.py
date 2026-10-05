"""Bootstrap Firestore approvals for administrators and existing allowlisted users.

Run by an operator, once, before switching a deployment to
``SIDE_B_ACCESS_STORE=firestore``. Nothing is written without ``--apply``.

    cd backend
    poetry run python scripts/bootstrap_access.py --project PROJECT_ID \
        --admin-uid ADMIN_UID [--approved-uid UID ...] [--approved-email EMAIL ...]
    # review the printed plan, then repeat the same command with --apply

Every UID is looked up in Firebase Authentication. Only existing, enabled
accounts with a verified email that sign in with Google are migrated; any other
input aborts the run before writing. Existing ``access_users`` documents are
never overwritten, so a stored rejection or block is preserved.

Credentials come from Application Default Credentials. The operator identity
needs Firebase Authentication read access and Firestore document write access
on the target project. No key file, token or password is read or printed.
"""

import argparse
import json
import sys
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.access import (  # noqa: E402
    AUDIT_COLLECTION,
    USERS_COLLECTION,
    InvalidAccessInputError,
    audit_document_id,
    validate_uid,
)

BOOTSTRAP_ACTOR = "bootstrap-migration"


@dataclass(frozen=True)
class Account:
    uid: str
    email: str | None
    display_name: str | None
    email_verified: bool
    disabled: bool
    providers: frozenset[str]


@dataclass(frozen=True)
class PlannedAccount:
    account: Account
    role: str  # "admin" or "user"


class BootstrapError(Exception):
    pass


def _account_from_record(record: Any) -> Account:
    providers = frozenset(
        str(getattr(info, "provider_id", "") or "")
        for info in (getattr(record, "provider_data", None) or [])
    )
    return Account(
        uid=str(record.uid),
        email=getattr(record, "email", None),
        display_name=getattr(record, "display_name", None),
        email_verified=bool(getattr(record, "email_verified", False)),
        disabled=bool(getattr(record, "disabled", False)),
        providers=providers,
    )


def resolve_accounts(
    auth_api: Any,
    *,
    admin_uids: Sequence[str],
    approved_uids: Sequence[str],
    approved_emails: Sequence[str],
) -> list[PlannedAccount]:
    """Look up every input in Firebase Auth and refuse anything unverifiable."""
    problems: list[str] = []
    planned: dict[str, PlannedAccount] = {}

    def add(account: Account, role: str) -> None:
        if account.disabled:
            problems.append(f"{account.uid}: account is disabled")
        elif "google.com" not in account.providers:
            problems.append(f"{account.uid}: not a Google sign-in account")
        elif not account.email_verified:
            problems.append(f"{account.uid}: email is not verified")
        current = planned.get(account.uid)
        if current is None or role == "admin":
            planned[account.uid] = PlannedAccount(account=account, role=role)

    if not admin_uids:
        problems.append("at least one --admin-uid is required")
    for role, uids in (("admin", admin_uids), ("user", approved_uids)):
        for uid in uids:
            try:
                validate_uid(uid)
            except InvalidAccessInputError:
                problems.append(f"{uid!r}: invalid UID")
                continue
            try:
                add(_account_from_record(auth_api.get_user(uid)), role)
            except Exception as exc:  # the SDK raises several lookup errors
                problems.append(f"{uid}: lookup failed ({type(exc).__name__})")
    for email in approved_emails:
        try:
            add(_account_from_record(auth_api.get_user_by_email(email)), "user")
        except Exception as exc:
            problems.append(f"{email}: lookup failed ({type(exc).__name__})")
    if problems:
        raise BootstrapError("; ".join(problems))
    return sorted(
        planned.values(), key=lambda item: (item.role != "admin", item.account.uid)
    )


def approved_document(account: Account, role: str, now: datetime) -> dict[str, Any]:
    return {
        "uid": account.uid,
        "email": account.email,
        "display_name": account.display_name,
        "status": "approved",
        "revision": 1,
        "requested_at": now,
        "updated_at": now,
        "decided_at": now,
        "decided_by": BOOTSTRAP_ACTOR,
        "migrated_from": "admin_bootstrap" if role == "admin" else "env_allowlist",
    }


def apply_bootstrap(
    db: Any,
    planned: Iterable[PlannedAccount],
    *,
    transactional: Callable[[Callable[..., Any]], Callable[..., Any]],
    now: datetime,
    dry_run: bool,
    operation_ids: Callable[[], str] = lambda: str(uuid.uuid4()),
) -> list[dict[str, Any]]:
    results = []
    for item in planned:
        account = item.account
        user_ref = db.collection(USERS_COLLECTION).document(account.uid)
        if dry_run:
            snapshot = user_ref.get()
            results.append(
                {
                    "uid": account.uid,
                    "role": item.role,
                    "result": "exists" if snapshot.exists else "would_create",
                    "status": (snapshot.to_dict() or {}).get("status")
                    if snapshot.exists
                    else "approved",
                }
            )
            continue
        operation_id = operation_ids()
        audit_ref = db.collection(AUDIT_COLLECTION).document(
            audit_document_id(BOOTSTRAP_ACTOR, operation_id)
        )

        def body(
            transaction,
            account=account,
            item=item,
            audit_ref=audit_ref,
            user_ref=user_ref,
            operation_id=operation_id,
        ):
            snapshot = user_ref.get(transaction=transaction)
            if snapshot.exists:
                return {
                    "result": "exists",
                    "status": (snapshot.to_dict() or {}).get("status"),
                }
            transaction.create(user_ref, approved_document(account, item.role, now))
            transaction.create(
                audit_ref,
                {
                    "operation_id": operation_id,
                    "actor_uid": BOOTSTRAP_ACTOR,
                    "target_uid": account.uid,
                    "action": "bootstrap_approve",
                    "expected_revision": 0,
                    "previous_status": "unregistered",
                    "new_status": "approved",
                    "previous_revision": 0,
                    "new_revision": 1,
                    "created_at": now,
                },
            )
            return {"result": "created", "status": "approved"}

        outcome = transactional(body)(db.transaction(max_attempts=5))
        results.append({"uid": account.uid, "role": item.role, **outcome})
    return results


def _default_auth_api(project: str):
    import firebase_admin
    from firebase_admin import auth

    name = f"side-b-bootstrap-{project}"
    try:
        app = firebase_admin.get_app(name)
    except ValueError:
        app = firebase_admin.initialize_app(options={"projectId": project}, name=name)

    class AuthApi:
        def get_user(self, uid):
            return auth.get_user(uid, app=app)

        def get_user_by_email(self, email):
            return auth.get_user_by_email(email, app=app)

    return AuthApi()


def _default_db(project: str, database: str):
    from google.cloud import firestore

    return firestore.Client(project=project, database=database), firestore.transactional


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create approved access_users documents for administrators and "
        "existing allowlisted users. Dry run unless --apply is given."
    )
    parser.add_argument("--project", required=True, help="Firebase/GCP project ID")
    parser.add_argument("--database", default="(default)", help="Firestore database ID")
    parser.add_argument(
        "--admin-uid",
        action="append",
        default=[],
        help="Server administrator UID (must also be in SIDE_B_ADMIN_UIDS). Repeatable.",
    )
    parser.add_argument(
        "--approved-uid", action="append", default=[], help="Existing allowlisted UID."
    )
    parser.add_argument(
        "--approved-email",
        action="append",
        default=[],
        help="Existing allowlisted email, resolved to its Firebase UID.",
    )
    parser.add_argument("--apply", action="store_true", help="Write the documents.")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    auth_api: Any = None,
    db_factory: Callable[[str, str], tuple[Any, Any]] | None = None,
    now: datetime | None = None,
    out=sys.stdout,
) -> int:
    args = parse_args(argv)
    try:
        planned = resolve_accounts(
            auth_api or _default_auth_api(args.project),
            admin_uids=args.admin_uid,
            approved_uids=args.approved_uid,
            approved_emails=args.approved_email,
        )
    except BootstrapError as exc:
        print(
            json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=out
        )
        return 2
    db, transactional = (db_factory or _default_db)(args.project, args.database)
    results = apply_bootstrap(
        db,
        planned,
        transactional=transactional,
        now=now or datetime.now(UTC),
        dry_run=not args.apply,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "applied": bool(args.apply),
                "project": args.project,
                "database": args.database,
                "accounts": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        file=out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
