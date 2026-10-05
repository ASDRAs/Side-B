"""HTTP next-page regressions using the production store and router, fake SDK only."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.services.access import (
    MAX_CURSOR_LENGTH,
    InvalidAccessInputError,
    ListCursor,
    decode_cursor,
    encode_cursor,
)
from tests.test_access_api import ADMIN, bearer, make_app
from tests.test_firestore_access_store import START, make_store, seed


@pytest.mark.parametrize("character", ["한", "😀", "\\", '"'])
@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "blocked"])
def test_maximum_uid_cursor_roundtrip(character, status):
    cursor = ListCursor(
        status, datetime(9999, 12, 31, 23, 59, 59, 999999, UTC), character * 128
    )
    encoded = encode_cursor(cursor)
    assert len(encoded) <= MAX_CURSOR_LENGTH
    assert decode_cursor(encoded, status) == cursor
    if character == "😀" and status in {"approved", "rejected"}:
        assert len(encoded) == MAX_CURSOR_LENGTH


@pytest.mark.parametrize("character", ["한", "😀"])
@pytest.mark.asyncio
async def test_http_next_page_with_maximum_multibyte_uid(character):
    store, db = make_store(admin_uids=frozenset({ADMIN}))
    for index in range(24):
        seed(
            db,
            f"listener-{index:02}",
            "pending",
            requested_at=START + timedelta(seconds=index),
        )
    boundary_uid = character * 128
    seed(db, boundary_uid, "pending", requested_at=START + timedelta(seconds=24))
    seed(db, "next-page", "pending", requested_at=START + timedelta(seconds=25))
    app, _ = make_app(store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get("/admin/access-users", headers=bearer(ADMIN))
        assert first.status_code == 200
        data = first.json()
        assert len(data["items"]) == 25
        assert data["items"][-1]["uid"] == boundary_uid
        cursor = data["next_cursor"]
        assert len(cursor) > 512  # This must exercise the original broken boundary.
        second = await client.get(
            "/admin/access-users", headers=bearer(ADMIN), params={"cursor": cursor}
        )
        assert second.status_code == 200, second.text
        assert [item["uid"] for item in second.json()["items"]] == ["next-page"]
        assert second.json()["next_cursor"] is None
        for invalid in [cursor + "!", "a" * (MAX_CURSOR_LENGTH + 1)]:
            rejected = await client.get(
                "/admin/access-users", headers=bearer(ADMIN), params={"cursor": invalid}
            )
            assert rejected.status_code == 422
    with pytest.raises(InvalidAccessInputError):
        decode_cursor("a" * (MAX_CURSOR_LENGTH + 1), "pending")
