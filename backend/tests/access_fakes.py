"""In-memory approval store for HTTP contract tests.

It applies the production policy functions (``plan_access_request`` and
``plan_decision``) under an asyncio lock. It verifies the API contract and the
policy, not Firestore transactions; those are covered by the emulator suite in
``tests/integration``.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from app.services.access import (
    AccessPage,
    AccessStoreUnavailableError,
    _page_from_records,
    audit_document_id,
    decode_cursor,
    plan_access_request,
    plan_decision,
    quota_document_id,
    record_from_data,
    validate_decision,
    validate_uid,
)


class InMemoryAccessStore:
    def __init__(self, *, admin_uids=frozenset(), daily_limit=50, start=None):
        self.admin_uids = frozenset(admin_uids)
        self.daily_limit = daily_limit
        self.users: dict[str, dict] = {}
        self.audits: dict[str, dict] = {}
        self.quota: dict[str, dict] = {}
        self.calls: list[str] = []
        self.failure: Exception | None = None
        self._now = start or datetime(2026, 10, 5, tzinfo=UTC)
        self._lock = asyncio.Lock()

    def now(self):
        self._now += timedelta(seconds=1)
        return self._now

    def seed(self, uid, status, *, revision=1, email=None):
        moment = self.now()
        self.users[uid] = {
            "uid": uid,
            "email": email or f"{uid}@example.com",
            "display_name": uid,
            "status": status,
            "revision": revision,
            "requested_at": moment,
            "updated_at": moment,
            "decided_at": None,
            "decided_by": None,
        }

    def _enter(self, name):
        self.calls.append(name)
        if self.failure is not None:
            raise self.failure

    async def get(self, uid):
        self._enter("get")
        data = self.users.get(validate_uid(uid))
        return record_from_data(uid, data) if data is not None else None

    async def request_access(self, identity):
        self._enter("request_access")
        async with self._lock:
            now = self.now()
            key = quota_document_id(now)
            plan = plan_access_request(
                identity,
                existing=self.users.get(identity.uid),
                quota=self.quota.get(key),
                daily_limit=self.daily_limit,
                now=now,
            )
            if plan.user_data is not None:
                self.users[identity.uid] = dict(plan.user_data)
                self.quota[key] = dict(plan.quota_data)
            return plan.result

    async def list_users(self, status, limit, cursor):
        self._enter("list_users")
        position = decode_cursor(cursor, status) if cursor else None
        records = sorted(
            (
                record_from_data(uid, data)
                for uid, data in self.users.items()
                if data["status"] == status
            ),
            key=lambda record: (record.requested_at, record.uid),
        )
        if position is not None:
            records = [
                record
                for record in records
                if (record.requested_at, record.uid)
                > (position.requested_at, position.uid)
            ]
        page = _page_from_records(status, records[: limit + 1], limit)
        return AccessPage(records=page.records, next_cursor=page.next_cursor)

    async def decide(self, request):
        self._enter("decide")
        validate_decision(request)
        async with self._lock:
            key = audit_document_id(request.actor_uid, request.operation_id)
            # Yield inside the critical section so concurrent callers really
            # interleave on the lock instead of finishing synchronously.
            await asyncio.sleep(0)
            plan = plan_decision(
                request,
                user=self.users.get(request.target_uid),
                audit=self.audits.get(key),
                admin_uids=self.admin_uids,
                now=self.now(),
            )
            if plan.user_update is not None:
                if key in self.audits:
                    raise AssertionError("audit written twice")
                self.users[request.target_uid].update(plan.user_update)
                self.audits[key] = dict(plan.audit_data)
            return plan.result


def outage():
    return AccessStoreUnavailableError("fixture outage")
