"""A small Firestore stand-in for exercising ``FirestoreAccessStore`` glue code.

It mimics only what the store uses: document get/create/update/set inside a
transaction, an optimistic version check at commit, the SDK's retry loop
(``Aborted`` re-runs the body with fresh buffered writes), and the list query.
Passing these tests does NOT prove real Firestore transaction semantics; the
emulator suite in ``tests/integration`` does that when an emulator is present.
"""

import copy


class FakeAborted(Exception):
    pass


class FakeAlreadyExists(Exception):
    pass


class FakeNotFound(Exception):
    pass


class Snapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


class DocRef:
    def __init__(self, db, collection, doc_id):
        self.db = db
        self.collection = collection
        self.id = doc_id
        self.path = f"{collection}/{doc_id}"

    def get(self, transaction=None, timeout=None):
        self.db.before_read(self, transaction)
        if transaction is not None:
            transaction.read_versions[self.path] = self.db.versions.get(self.path, 0)
        return Snapshot(self.id, copy.deepcopy(self.db.data.get(self.path)))


class Query:
    def __init__(self, db, collection):
        self.db = db
        self.collection = collection
        self.filters = []
        self.orders = []
        self.max_results = None
        self.after = None

    def _copy(self):
        clone = Query(self.db, self.collection)
        clone.filters = list(self.filters)
        clone.orders = list(self.orders)
        clone.max_results = self.max_results
        clone.after = self.after
        return clone

    def where(self, *, filter):
        clone = self._copy()
        assert filter.op_string == "=="
        clone.filters.append((filter.field_path, filter.value))
        return clone

    def order_by(self, field):
        clone = self._copy()
        clone.orders.append(field)
        return clone

    def limit(self, count):
        clone = self._copy()
        clone.max_results = count
        return clone

    def start_after(self, values):
        clone = self._copy()
        clone.after = values
        return clone

    def _key(self, doc_id, data):
        return tuple(
            doc_id if field == "__name__" else data.get(field) for field in self.orders
        )

    def stream(self, timeout=None):
        self.db.before_query(self)
        prefix = f"{self.collection}/"
        rows = []
        for path, data in self.db.data.items():
            if not path.startswith(prefix):
                continue
            if all(data.get(field) == value for field, value in self.filters):
                if all(field == "__name__" or field in data for field in self.orders):
                    rows.append((path.removeprefix(prefix), data))
        rows.sort(key=lambda row: self._key(*row))
        if self.after is not None:
            cutoff = tuple(self.after[field] for field in self.orders)
            rows = [row for row in rows if self._key(*row) > cutoff]
        if self.max_results is not None:
            rows = rows[: self.max_results]
        for doc_id, data in rows:
            yield Snapshot(doc_id, copy.deepcopy(data))


class Collection:
    def __init__(self, db, name):
        self.db = db
        self.name = name

    def document(self, doc_id):
        return DocRef(self.db, self.name, doc_id)

    def where(self, *, filter):
        return Query(self.db, self.name).where(filter=filter)


class Transaction:
    def __init__(self, db, max_attempts):
        self.db = db
        self.max_attempts = max_attempts
        self.writes = []
        self.read_versions = {}

    def create(self, ref, data):
        self.writes.append(("create", ref.path, copy.deepcopy(data)))

    def update(self, ref, data):
        self.writes.append(("update", ref.path, copy.deepcopy(data)))

    def set(self, ref, data):
        self.writes.append(("set", ref.path, copy.deepcopy(data)))


class FakeFirestore:
    def __init__(self):
        self.data = {}
        self.versions = {}
        self.commits = []
        self.attempts = 0
        self.read_hook = None
        self.query_hook = None
        self.commit_hook = None
        self.abort_commits = 0

    def collection(self, name):
        return Collection(self, name)

    def transaction(self, max_attempts):
        return Transaction(self, max_attempts)

    def before_read(self, ref, transaction):
        if self.read_hook is not None:
            self.read_hook(ref, transaction)

    def before_query(self, query):
        if self.query_hook is not None:
            self.query_hook(query)

    def write_now(self, path, data):
        """A write from another client, outside any store transaction."""
        self.data[path] = copy.deepcopy(data)
        self.versions[path] = self.versions.get(path, 0) + 1

    def commit(self, transaction):
        if self.commit_hook is not None:
            self.commit_hook(transaction)
        if self.abort_commits:
            self.abort_commits -= 1
            raise FakeAborted("injected contention")
        for path, version in transaction.read_versions.items():
            if self.versions.get(path, 0) != version:
                raise FakeAborted("document changed since it was read")
        for kind, path, _data in transaction.writes:
            if kind == "create" and path in self.data:
                raise FakeAlreadyExists(path)
            if kind == "update" and path not in self.data:
                raise FakeNotFound(path)
        for kind, path, data in transaction.writes:
            if kind == "update":
                merged = dict(self.data[path])
                merged.update(data)
                data = merged
            self.data[path] = data
            self.versions[path] = self.versions.get(path, 0) + 1
        self.commits.append(list(transaction.writes))


def fake_transactional(body):
    """Mirror of google.cloud.firestore_v1.transaction._Transactional.__call__."""

    def run(transaction):
        for _attempt in range(transaction.max_attempts):
            transaction.db.attempts += 1
            transaction.writes = []
            transaction.read_versions = {}
            result = body(transaction)
            try:
                transaction.db.commit(transaction)
                return result
            except FakeAborted:
                continue
        raise ValueError(
            f"Failed to commit transaction in {transaction.max_attempts} attempts."
        )

    return run
