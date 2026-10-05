const assert = require("node:assert/strict");
const test = require("node:test");

const load = () => import("../scripts/accessView.js");
const signedIn = (access, mode = "firebase") => ({ mode, status: "signed_in", access });

test("only a confirmed approval opens the feature UI in managed modes", async () => {
  const { isAccessGated } = await load();
  for (const status of ["unregistered", "pending", "rejected", "blocked", "unavailable"]) {
    assert.equal(isAccessGated(signedIn({ status })), true, status);
  }
  assert.equal(isAccessGated(signedIn({ status: "approved" })), false);
  assert.equal(isAccessGated(signedIn(null)), true);
  assert.equal(isAccessGated({ mode: "legacy", status: "legacy" }), false);
  assert.equal(isAccessGated({ mode: "firebase", status: "signed_out" }), false);
});

test("each status has its own screen and only unregistered accounts may request", async () => {
  const { accessGateView } = await load();
  const views = ["unregistered", "pending", "rejected", "blocked", "unavailable"]
    .map((status) => accessGateView({ status, store: "firestore", requestedAt: "2026-10-05T00:00:00Z" }));
  assert.equal(new Set(views.map((view) => view.title)).size, 5);
  assert.deepEqual(views.map((view) => view.canRequest), [true, false, false, false, false]);
  assert.deepEqual(views.map((view) => view.showRequestedAt), [false, true, true, true, false]);
  assert.equal(accessGateView({ status: "unregistered", store: "env" }).canRequest, false);
  assert.equal(accessGateView({ status: "toString" }).status, "unavailable");
});

test("approval denials are told apart from identity and allowlist rejections", async () => {
  const { accessDenialStatus } = await load();
  assert.equal(accessDenialStatus(403, { detail: { code: "access_not_approved", access_status: "blocked" } }), "blocked");
  assert.equal(accessDenialStatus(403, { detail: { code: "access_not_approved" } }), "unavailable");
  assert.equal(accessDenialStatus(403, { detail: { code: "auth_account_denied" } }), null);
  assert.equal(accessDenialStatus(403, { detail: { code: "auth_identity_unverified" } }), null);
  assert.equal(accessDenialStatus(401, { detail: { code: "access_not_approved" } }), null);
  assert.equal(accessDenialStatus(403, null), null);
});

test("timestamps render only from valid server values", async () => {
  const { formatTimestamp } = await load();
  assert.notEqual(formatTimestamp("2026-10-05T01:02:03Z"), "");
  assert.equal(formatTimestamp("not a date"), "");
  assert.equal(formatTimestamp(null), "");
});
