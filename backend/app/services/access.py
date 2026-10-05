"""Server-side account approval, kept separate from Firebase identity.

Identity (token signature, project, Google provider, verified email) is checked
in ``auth.py``. This module only answers "may this verified UID use Side-B?"
and records administrator decisions. The approval state lives in Firestore and
is written only by this backend through the server SDK; clients never touch
Firestore directly (``deployment/firestore/firestore.rules`` denies them).

Policy decisions are pure functions (``plan_*``) so the same rules run inside a
real Firestore transaction and in the in-memory store used by HTTP tests.
"""

import asyncio
import base64
import binascii
import json
import logging
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, TypeVar

logger = logging.getLogger(__name__)

AccessStatus = Literal["unregistered", "pending", "approved", "rejected", "blocked"]
StoredStatus = Literal["pending", "approved", "rejected", "blocked"]
DecisionAction = Literal["approve", "reject", "block", "unblock", "reopen"]

STORED_STATUSES: tuple[StoredStatus, ...] = (
    "pending",
    "approved",
    "rejected",
    "blocked",
)
# Administrator transitions. ``unregistered -> pending`` is deliberately absent:
# only the account owner may create its own request.
TRANSITIONS: Mapping[DecisionAction, tuple[StoredStatus, StoredStatus]] = {
    "approve": ("pending", "approved"),
    "reject": ("pending", "rejected"),
    "block": ("approved", "blocked"),
    "unblock": ("blocked", "approved"),
    "reopen": ("rejected", "pending"),
}

USERS_COLLECTION = "access_users"
AUDIT_COLLECTION = "access_audit"
QUOTA_COLLECTION = "access_request_quota"

MAX_UID_LENGTH = 128
MAX_EMAIL_LENGTH = 320
MAX_DISPLAY_NAME_LENGTH = 200
MAX_REVISION = 2**53 - 1
# Compact JSON contains at most 512 UID bytes (128 Unicode scalars x 4 UTF-8
# bytes), an 8-byte status and the 32-byte UTC timestamp including microseconds.
# Quotes/backslashes use 2 JSON bytes per scalar, below the 4-byte UTF-8 bound;
# control characters that could require 6-byte escapes are forbidden in UIDs.
MAX_CURSOR_JSON_BYTES = MAX_UID_LENGTH * 4 + len(
    json.dumps(
        {
            "s": max(STORED_STATUSES, key=len),
            "t": datetime.max.replace(tzinfo=UTC).isoformat(),
            "u": "",
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
)
MAX_CURSOR_LENGTH = (MAX_CURSOR_JSON_BYTES * 4 + 2) // 3  # unpadded base64url

T = TypeVar("T")


class AccessError(Exception):
    """Base class for policy outcomes that are not store outages."""


class InvalidAccessInputError(AccessError, ValueError):
    pass


class AccessUserNotFoundError(AccessError):
    pass


class AccessTargetProtectedError(AccessError):
    """Administrator accounts are changed through server settings only."""


class AccessRequestQuotaExceededError(AccessError):
    pass


class AccessConflictError(AccessError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        current_status: str | None = None,
        current_revision: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.current_status = current_status
        self.current_revision = current_revision


class AccessStoreUnavailableError(Exception):
    """The approval store could not answer. Callers must fail closed (503)."""


class AccessRecordInvalidError(AccessStoreUnavailableError):
    """A stored document does not match the schema; never treat it as approved."""


def validate_uid(value: object) -> str:
    """Validate a UID used as a Firestore document ID.

    Firebase UIDs are 1-128 characters and are not limited to ASCII, so no
    character-class allowlist is applied. Only the document-ID path rules are
    enforced: no ``/``, not ``.``/``..``, not ``__reserved__`` and valid UTF-8.
    """
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_UID_LENGTH:
        raise InvalidAccessInputError("uid must be 1-128 characters")
    if "/" in value or value in {".", ".."}:
        raise InvalidAccessInputError("uid is not a valid document id")
    if value.startswith("__") and value.endswith("__"):
        raise InvalidAccessInputError("uid is not a valid document id")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise InvalidAccessInputError("uid contains control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidAccessInputError("uid is not valid UTF-8") from exc
    return value


def validate_operation_id(value: object) -> str:
    """Accept only a canonical UUID so the audit document ID stays path-safe."""
    if not isinstance(value, str) or len(value) != 36:
        raise InvalidAccessInputError("operation_id must be a UUID")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise InvalidAccessInputError("operation_id must be a UUID") from exc
    if str(parsed) != value.lower():
        raise InvalidAccessInputError("operation_id must be a UUID")
    return str(parsed)


def audit_document_id(actor_uid: str, operation_id: str) -> str:
    """Idempotency key. An operation ID only replays for the same administrator."""
    return f"{validate_uid(actor_uid)}:{validate_operation_id(operation_id)}"


def _bounded_text(value: object, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] or None


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    raise AccessRecordInvalidError("timestamp field has an invalid type")


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class AccessIdentity:
    """Display data copied from a verified token, never from a request body."""

    uid: str
    email: str | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class AccessRecord:
    uid: str
    status: StoredStatus
    revision: int
    email: str | None = None
    display_name: str | None = None
    requested_at: datetime | None = None
    updated_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "status": self.status,
            "revision": self.revision,
            "email": self.email,
            "display_name": self.display_name,
            "requested_at": isoformat(self.requested_at),
            "updated_at": isoformat(self.updated_at),
            "decided_at": isoformat(self.decided_at),
            "decided_by": self.decided_by,
        }


def record_from_data(uid: str, data: Mapping[str, Any]) -> AccessRecord:
    status = data.get("status")
    revision = data.get("revision")
    if status not in STORED_STATUSES:
        raise AccessRecordInvalidError("access record has an unknown status")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or not 0 <= revision <= MAX_REVISION
    ):
        raise AccessRecordInvalidError("access record has an invalid revision")
    decided_by = data.get("decided_by")
    return AccessRecord(
        uid=uid,
        status=status,
        revision=revision,
        email=_bounded_text(data.get("email"), MAX_EMAIL_LENGTH),
        display_name=_bounded_text(data.get("display_name"), MAX_DISPLAY_NAME_LENGTH),
        requested_at=_timestamp(data.get("requested_at")),
        updated_at=_timestamp(data.get("updated_at")),
        decided_at=_timestamp(data.get("decided_at")),
        decided_by=str(decided_by) if decided_by else None,
    )


@dataclass(frozen=True)
class AccessRequestResult:
    record: AccessRecord
    created: bool


@dataclass(frozen=True)
class AccessRequestPlan:
    result: AccessRequestResult
    user_data: dict[str, Any] | None = None
    quota_data: dict[str, Any] | None = None


def quota_document_id(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%d")


def plan_access_request(
    identity: AccessIdentity,
    *,
    existing: Mapping[str, Any] | None,
    quota: Mapping[str, Any] | None,
    daily_limit: int,
    now: datetime,
) -> AccessRequestPlan:
    """Create a pending request once. Repeats never change an existing state."""
    uid = validate_uid(identity.uid)
    if existing is not None:
        return AccessRequestPlan(
            result=AccessRequestResult(record_from_data(uid, existing), created=False)
        )
    used = (quota or {}).get("count", 0)
    if isinstance(used, bool) or not isinstance(used, int) or used < 0:
        raise AccessRecordInvalidError("request quota counter is invalid")
    if used >= daily_limit:
        raise AccessRequestQuotaExceededError("daily access request limit reached")
    user_data = {
        "uid": uid,
        "email": _bounded_text(identity.email, MAX_EMAIL_LENGTH),
        "display_name": _bounded_text(identity.display_name, MAX_DISPLAY_NAME_LENGTH),
        "status": "pending",
        "revision": 1,
        "requested_at": now,
        "updated_at": now,
        "decided_at": None,
        "decided_by": None,
    }
    return AccessRequestPlan(
        result=AccessRequestResult(record_from_data(uid, user_data), created=True),
        user_data=user_data,
        quota_data={
            "date": quota_document_id(now),
            "count": used + 1,
            "updated_at": now,
        },
    )


@dataclass(frozen=True)
class DecisionRequest:
    actor_uid: str
    target_uid: str
    action: DecisionAction
    expected_revision: int
    operation_id: str


@dataclass(frozen=True)
class DecisionResult:
    target_uid: str
    action: DecisionAction
    previous_status: StoredStatus
    status: StoredStatus
    revision: int
    operation_id: str
    decided_at: datetime | None
    replayed: bool

    def public(self) -> dict[str, Any]:
        return {
            "uid": self.target_uid,
            "action": self.action,
            "previous_status": self.previous_status,
            "status": self.status,
            "revision": self.revision,
            "operation_id": self.operation_id,
            "decided_at": isoformat(self.decided_at),
            "replayed": self.replayed,
        }


@dataclass(frozen=True)
class DecisionPlan:
    result: DecisionResult
    user_update: dict[str, Any] | None = None
    audit_data: dict[str, Any] | None = None


def validate_decision(request: DecisionRequest) -> DecisionRequest:
    validate_uid(request.actor_uid)
    validate_uid(request.target_uid)
    validate_operation_id(request.operation_id)
    if request.action not in TRANSITIONS:
        raise InvalidAccessInputError("unsupported action")
    revision = request.expected_revision
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise InvalidAccessInputError("expected_revision must be an integer")
    if not 0 <= revision <= MAX_REVISION:
        raise InvalidAccessInputError("expected_revision is out of range")
    return request


def plan_decision(
    request: DecisionRequest,
    *,
    user: Mapping[str, Any] | None,
    audit: Mapping[str, Any] | None,
    admin_uids: frozenset[str],
    now: datetime,
) -> DecisionPlan:
    """Decide one administrator transition, or replay an identical earlier one.

    The caller must already have re-verified that ``actor_uid`` is a current
    administrator; a replay never skips that check.
    """
    validate_decision(request)
    if audit is not None:
        same = (
            audit.get("actor_uid") == request.actor_uid
            and audit.get("target_uid") == request.target_uid
            and audit.get("action") == request.action
            and audit.get("expected_revision") == request.expected_revision
        )
        if not same:
            raise AccessConflictError(
                "access_operation_conflict",
                "이미 다른 작업에 사용된 작업 ID입니다.",
            )
        previous = audit.get("previous_status")
        status = audit.get("new_status")
        revision = audit.get("new_revision")
        if previous not in STORED_STATUSES or status not in STORED_STATUSES:
            raise AccessRecordInvalidError("audit record has an invalid status")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise AccessRecordInvalidError("audit record has an invalid revision")
        return DecisionPlan(
            result=DecisionResult(
                target_uid=request.target_uid,
                action=request.action,
                previous_status=previous,
                status=status,
                revision=revision,
                operation_id=request.operation_id,
                decided_at=_timestamp(audit.get("created_at")),
                replayed=True,
            )
        )

    if request.target_uid in admin_uids:
        raise AccessTargetProtectedError(
            "administrator accounts are managed by settings"
        )
    if user is None:
        raise AccessUserNotFoundError("access request not found")
    current = record_from_data(request.target_uid, user)
    if current.revision != request.expected_revision:
        raise AccessConflictError(
            "access_revision_conflict",
            "다른 작업이 먼저 처리되었습니다. 최신 상태를 다시 불러오세요.",
            current_status=current.status,
            current_revision=current.revision,
        )
    source, target = TRANSITIONS[request.action]
    if current.status != source:
        raise AccessConflictError(
            "access_invalid_transition",
            "현재 상태에서는 이 작업을 할 수 없습니다.",
            current_status=current.status,
            current_revision=current.revision,
        )
    if current.revision >= MAX_REVISION:
        raise AccessRecordInvalidError("access record revision is exhausted")

    revision = current.revision + 1
    user_update = {
        "status": target,
        "revision": revision,
        "updated_at": now,
        "decided_at": now,
        "decided_by": request.actor_uid,
    }
    # No credentials, tokens or request headers are ever written here.
    audit_data = {
        "operation_id": request.operation_id,
        "actor_uid": request.actor_uid,
        "target_uid": request.target_uid,
        "action": request.action,
        "expected_revision": request.expected_revision,
        "previous_status": current.status,
        "new_status": target,
        "previous_revision": current.revision,
        "new_revision": revision,
        "created_at": now,
    }
    return DecisionPlan(
        result=DecisionResult(
            target_uid=request.target_uid,
            action=request.action,
            previous_status=current.status,
            status=target,
            revision=revision,
            operation_id=request.operation_id,
            decided_at=now,
            replayed=False,
        ),
        user_update=user_update,
        audit_data=audit_data,
    )


@dataclass(frozen=True)
class ListCursor:
    status: StoredStatus
    requested_at: datetime
    uid: str


def encode_cursor(cursor: ListCursor) -> str:
    payload = json.dumps(
        {
            "s": cursor.status,
            "t": cursor.requested_at.astimezone(UTC).isoformat(),
            "u": cursor.uid,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    if len(encoded) > MAX_CURSOR_LENGTH:
        raise InvalidAccessInputError("cursor is invalid")
    return encoded


def decode_cursor(value: str, status: StoredStatus) -> ListCursor:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_CURSOR_LENGTH:
        raise InvalidAccessInputError("cursor is invalid")
    try:
        raw = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
        data = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidAccessInputError("cursor is invalid") from exc
    if not isinstance(data, dict) or set(data) != {"s", "t", "u"}:
        raise InvalidAccessInputError("cursor is invalid")
    if data["s"] != status:
        raise InvalidAccessInputError("cursor belongs to another status filter")
    try:
        requested_at = datetime.fromisoformat(data["t"])
    except (TypeError, ValueError) as exc:
        raise InvalidAccessInputError("cursor is invalid") from exc
    if requested_at.tzinfo is None:
        raise InvalidAccessInputError("cursor is invalid")
    return ListCursor(
        status=status, requested_at=requested_at, uid=validate_uid(data["u"])
    )


@dataclass(frozen=True)
class AccessPage:
    records: tuple[AccessRecord, ...]
    next_cursor: str | None


class AccessStore(Protocol):
    async def get(self, uid: str) -> AccessRecord | None: ...

    async def request_access(self, identity: AccessIdentity) -> AccessRequestResult: ...

    async def list_users(
        self, status: StoredStatus, limit: int, cursor: str | None
    ) -> AccessPage: ...

    async def decide(self, request: DecisionRequest) -> DecisionResult: ...


def _page_from_records(
    status: StoredStatus, records: list[AccessRecord], limit: int
) -> AccessPage:
    page = records[:limit]
    next_cursor = None
    if len(records) > limit and page and page[-1].requested_at is not None:
        last = page[-1]
        next_cursor = encode_cursor(
            ListCursor(status=status, requested_at=last.requested_at, uid=last.uid)
        )
    return AccessPage(records=tuple(page), next_cursor=next_cursor)


class FirestoreAccessStore:
    """Firestore Standard store using the server SDK and the runtime identity.

    Credentials come from Application Default Credentials (the Cloud Run
    service account). No key file is read. Each blocking SDK call runs in a
    worker thread; a semaphore bounds concurrent calls and a slot stays
    occupied until its thread really finishes, like ``FirebaseTokenVerifier``.
    """

    def __init__(
        self,
        *,
        project_id: str,
        database: str,
        admin_uids: frozenset[str],
        request_daily_limit: int,
        operation_timeout_seconds: float = 8.0,
        max_concurrency: int = 8,
        transaction_attempts: int = 5,
        client_factory: Callable[[], Any] | None = None,
        transactional: Callable[[Callable[..., Any]], Callable[..., Any]] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.project_id = str(project_id or "").strip()
        self.database = str(database or "(default)").strip() or "(default)"
        self._admin_uids = admin_uids
        self._daily_limit = max(1, request_daily_limit)
        self._timeout = max(1.0, operation_timeout_seconds)
        self._rpc_timeout = max(1.0, self._timeout - 0.5)
        self._slots = asyncio.Semaphore(max(1, max_concurrency))
        self._attempts = max(1, transaction_attempts)
        self._client_factory = client_factory
        self._transactional = transactional
        self._clock = clock
        self._client = None
        self._client_lock = threading.Lock()

    def _db(self):
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is None:
                if self._client_factory is not None:
                    self._client = self._client_factory()
                else:
                    from google.cloud import firestore

                    self._client = firestore.Client(
                        project=self.project_id or None, database=self.database
                    )
        return self._client

    def _transaction_wrapper(self):
        if self._transactional is not None:
            return self._transactional
        from google.cloud import firestore

        return firestore.transactional

    @staticmethod
    def _guard(operation: Callable[[], T]) -> Callable[[], T]:
        def run() -> T:
            try:
                return operation()
            except (AccessError, AccessStoreUnavailableError):
                raise
            except Exception as exc:
                # SDK messages can include resource paths; never request data.
                logger.warning("Access store operation failed (%s)", type(exc).__name__)
                raise AccessStoreUnavailableError(
                    "access store is unavailable"
                ) from exc

        return run

    async def _run(self, operation: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=self._timeout)
        except TimeoutError as exc:
            raise AccessStoreUnavailableError(
                "access store capacity exhausted"
            ) from exc
        future = loop.run_in_executor(None, self._guard(operation))
        future.add_done_callback(lambda _future: self._slots.release())
        remaining = max(0.001, deadline - loop.time())
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=remaining)
        except TimeoutError as exc:
            # A decision may still commit after this point. Clients retry with
            # the same operation ID, which replays instead of applying twice.
            raise AccessStoreUnavailableError("access store timed out") from exc

    def _users(self):
        return self._db().collection(USERS_COLLECTION)

    async def get(self, uid: str) -> AccessRecord | None:
        uid = validate_uid(uid)

        def read():
            snapshot = self._users().document(uid).get(timeout=self._rpc_timeout)
            return (
                record_from_data(uid, snapshot.to_dict() or {})
                if snapshot.exists
                else None
            )

        return await self._run(read)

    async def request_access(self, identity: AccessIdentity) -> AccessRequestResult:
        validate_uid(identity.uid)

        def write():
            db = self._db()
            now = self._clock()
            user_ref = db.collection(USERS_COLLECTION).document(identity.uid)
            quota_ref = db.collection(QUOTA_COLLECTION).document(quota_document_id(now))

            def body(transaction):
                # Firestore requires every read before the first write.
                user = user_ref.get(transaction=transaction, timeout=self._rpc_timeout)
                quota = None
                if not user.exists:
                    quota_snapshot = quota_ref.get(
                        transaction=transaction, timeout=self._rpc_timeout
                    )
                    quota = quota_snapshot.to_dict() if quota_snapshot.exists else None
                plan = plan_access_request(
                    identity,
                    existing=(user.to_dict() or {}) if user.exists else None,
                    quota=quota,
                    daily_limit=self._daily_limit,
                    now=now,
                )
                if plan.user_data is not None:
                    transaction.create(user_ref, plan.user_data)
                    transaction.set(quota_ref, plan.quota_data)
                return plan.result

            transaction = db.transaction(max_attempts=self._attempts)
            return self._transaction_wrapper()(body)(transaction)

        return await self._run(write)

    async def list_users(
        self, status: StoredStatus, limit: int, cursor: str | None
    ) -> AccessPage:
        if status not in STORED_STATUSES:
            raise InvalidAccessInputError("unsupported status")
        if not 1 <= limit <= 50:
            raise InvalidAccessInputError("limit must be 1-50")
        position = decode_cursor(cursor, status) if cursor else None

        def read():
            from google.cloud.firestore_v1.base_query import FieldFilter
            from google.cloud.firestore_v1.field_path import FieldPath

            query = (
                self._users()
                .where(filter=FieldFilter("status", "==", status))
                .order_by("requested_at")
                .order_by(FieldPath.document_id())
                .limit(limit + 1)
            )
            if position is not None:
                query = query.start_after(
                    {"requested_at": position.requested_at, "__name__": position.uid}
                )
            records = [
                record_from_data(snapshot.id, snapshot.to_dict() or {})
                for snapshot in query.stream(timeout=self._rpc_timeout)
            ]
            return _page_from_records(status, records, limit)

        return await self._run(read)

    async def decide(self, request: DecisionRequest) -> DecisionResult:
        validate_decision(request)
        if request.target_uid in self._admin_uids:
            raise AccessTargetProtectedError(
                "administrator accounts are managed by settings"
            )

        def write():
            db = self._db()
            user_ref = db.collection(USERS_COLLECTION).document(request.target_uid)
            audit_ref = db.collection(AUDIT_COLLECTION).document(
                audit_document_id(request.actor_uid, request.operation_id)
            )

            def body(transaction):
                audit = audit_ref.get(
                    transaction=transaction, timeout=self._rpc_timeout
                )
                user = user_ref.get(transaction=transaction, timeout=self._rpc_timeout)
                plan = plan_decision(
                    request,
                    user=(user.to_dict() or {}) if user.exists else None,
                    audit=(audit.to_dict() or {}) if audit.exists else None,
                    admin_uids=self._admin_uids,
                    now=self._clock(),
                )
                if plan.user_update is not None:
                    # One commit: state and audit succeed or fail together. The
                    # deterministic audit ID plus create() makes a retried or
                    # replayed commit unable to add a second audit record.
                    transaction.update(user_ref, plan.user_update)
                    transaction.create(audit_ref, plan.audit_data)
                return plan.result

            transaction = db.transaction(max_attempts=self._attempts)
            return self._transaction_wrapper()(body)(transaction)

        return await self._run(write)
