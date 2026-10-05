const assert = require("node:assert/strict");
const test = require("node:test");

const load = () => import("../scripts/accessAdmin.js");
const API = "https://api.example";
const json = (body, status = 200) => new Response(JSON.stringify(body), { status });
const item = (status, extra = {}) => ({ uid: `uid-${status}`, status, revision: 3, email: "x@example.com", is_admin: false, ...extra });

test("actions follow the server state machine and never touch administrators", async () => {
  const { actionsFor } = await load();
  const names = (status, extra) => actionsFor(item(status, extra)).map(({ action }) => action);
  assert.deepEqual(names("pending"), ["approve", "reject"]);
  assert.deepEqual(names("approved"), ["block"]);
  assert.deepEqual(names("blocked"), ["unblock"]);
  assert.deepEqual(names("rejected"), ["reopen"]);
  assert.deepEqual(names("approved", { is_admin: true }), []);
  assert.deepEqual(actionsFor({ status: "superuser" }), []);
});

test("list requests are bounded, filtered and cursor-based", async () => {
  const { listAccessUsers } = await load();
  const urls = [];
  const fetchImpl = async (url) => {
    urls.push(new URL(url));
    return json({ items: [item("pending")], next_cursor: "next-token" });
  };
  const page = await listAccessUsers(fetchImpl, API, { status: "pending" });
  await listAccessUsers(fetchImpl, API, { status: "pending", cursor: page.nextCursor });
  assert.equal(urls[0].pathname, "/admin/access-users");
  assert.equal(urls[0].searchParams.get("limit"), "25");
  assert.equal(urls[0].searchParams.get("status"), "pending");
  assert.equal(urls[0].searchParams.has("cursor"), false);
  assert.equal(urls[1].searchParams.get("cursor"), "next-token");
  assert.equal(page.items.length, 1);
});

test("malformed list responses are rejected instead of rendered", async () => {
  const { listAccessUsers } = await load();
  for (const body of [{}, { items: [{ uid: 1 }] }, { items: [{ uid: "x", status: "owner", revision: 1 }] }]) {
    await assert.rejects(listAccessUsers(async () => json(body), API, { status: "pending" }), /형식/);
  }
});

test("decisions send only the allowlisted action, revision and operation ID", async () => {
  const { decideAccess } = await load();
  let seen;
  const fetchImpl = async (url, init) => { seen = { url, init }; return json({ status: "approved" }); };
  await decideAccess(fetchImpl, API, {
    uid: "a/b?c", action: "approve", expectedRevision: 3, operationId: "6f1d2b8e-3c4a-4e5f-9a7b-1c2d3e4f5a6b",
  });
  assert.equal(seen.url, `${API}/admin/access-users/a%2Fb%3Fc/decision`);
  assert.deepEqual(JSON.parse(seen.init.body), {
    action: "approve", expected_revision: 3, operation_id: "6f1d2b8e-3c4a-4e5f-9a7b-1c2d3e4f5a6b",
  });
});

test("server failures map to reload, revoke or retry without claiming success", async () => {
  const { AdminApiError, adminErrorOutcome, decideAccess } = await load();
  const outcomes = {};
  for (const [status, code] of [
    [409, "access_revision_conflict"], [409, "access_operation_conflict"], [404, "access_user_not_found"],
    [403, "admin_required"], [404, "access_management_disabled"], [401, "auth_unauthorized"],
    [403, "access_admin_target_protected"], [429, "auth_rate_limited"], [503, "access_store_unavailable"],
    [422, "access_invalid_input"],
  ]) {
    const error = await decideAccess(async () => json({ detail: { code, message: code } }, status), API, {
      uid: "u", action: "approve", expectedRevision: 1, operationId: "x",
    }).catch((caught) => caught);
    assert.ok(error instanceof AdminApiError);
    outcomes[`${status}:${code}`] = adminErrorOutcome(error);
  }
  assert.equal(outcomes["409:access_revision_conflict"].reload, true);
  assert.equal(outcomes["409:access_operation_conflict"].reload, true);
  assert.equal(outcomes["404:access_user_not_found"].reload, true);
  assert.equal(outcomes["403:admin_required"].revoke, true);
  assert.equal(outcomes["404:access_management_disabled"].revoke, true);
  assert.equal(outcomes["401:auth_unauthorized"].revoke, true);
  assert.equal(outcomes["403:access_admin_target_protected"].retry, false);
  assert.equal(outcomes["429:auth_rate_limited"].retry, true);
  assert.equal(outcomes["503:access_store_unavailable"].retry, true);
  assert.equal(outcomes["422:access_invalid_input"].retry, false);
  const network = await decideAccess(async () => { throw new TypeError("offline"); }, API, {
    uid: "u", action: "approve", expectedRevision: 1, operationId: "x",
  }).catch((caught) => caught);
  assert.equal(adminErrorOutcome(network).retry, true);
});

test("aborted administrator requests stay aborts", async () => {
  const { listAccessUsers } = await load();
  const abort = new DOMException("stop", "AbortError");
  await assert.rejects(listAccessUsers(async () => { throw abort; }, API, { status: "pending" }), { name: "AbortError" });
});
