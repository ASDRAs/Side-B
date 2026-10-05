// Actual dist worker + background.js. SDK/network use explicit local fixtures.
const assert = require("node:assert/strict");
const test = require("node:test");
const { repairHarness, ORIGIN, settle, deferred, me, response } = require("./helpers/repairHarness.cjs");

test("repair: A 401 refresh cannot return the administrator token to member B", async () => {
  const h = repairHarness();
  await h.login();
  const refresh = deferred();
  h.setToken((user, force) => user === h.a && force ? refresh.promise : `fixture-${user.uid}`);
  const pendingA = h.credential({ forceRefresh: true, rejectedToken: "fixture-A" });
  const rejectedA = assert.rejects(pendingA, /계정이 변경/);
  await settle();
  h.responses.push(me("B", "approved", false));
  h.switchTo(h.b);
  await settle();
  const pendingB = h.credential({ forceRefresh: true, rejectedToken: "fixture-B" });
  refresh.resolve("fixture-admin-A-refreshed");
  await rejectedA;
  const b = (await pendingB).credential;
  assert.equal(b.headers.Authorization, "Bearer fixture-B");
  assert.equal(h.manager.state().access.canManage, false);
});

test("repair: account observer clears old approval immediately while B validation is pending", async () => {
  const h = repairHarness();
  await h.login();
  const gate = deferred();
  h.responses.push(() => gate.promise);
  h.switchTo(h.b);
  assert.equal(h.manager.state().account, null);
  assert.equal(h.manager.state().access, null);
  await assert.rejects(h.credential(), /로그인|계정/);
  gate.resolve(me("B", "pending", false));
  await settle();
  assert.equal(h.manager.state().account.uid, "B");
});

test("repair: delayed SDK observer cannot issue credentials from unvalidated B", async () => {
  const h = repairHarness();
  await h.login();
  h.switchTo(h.b, false);
  await assert.rejects(h.credential(), /로그인|계정/);
});

for (const operation of ["AUTH_REFRESH_ACCESS", "AUTH_REQUEST_ACCESS"]) {
 for (const status of ["blocked", "approved"]) {
  test(`repair: stale ${operation} cannot restore admin after a newer ${status} observer`, async () => {
    const h = repairHarness();
    await h.login();
    const gate = deferred();
    h.responses.push(() => gate.promise);
    const old = h.send({ type: operation });
    await settle();
    h.responses.push(me("A", status, false));
    h.observe();
    await settle();
    assert.equal(h.manager.state().access.status, status);
    gate.resolve(operation === "AUTH_REFRESH_ACCESS" ? me() : response(200, { access_status: "approved" }));
    await old;
    assert.equal(h.manager.state().access.status, status);
    assert.equal(h.manager.state().access.canManage, false);
  });
 }
}

test("repair: actual authenticatedFetch A401 then B login never retries A with B or leaks A credentials", async () => {
  const h = repairHarness();
  await h.login();
  globalThis.chrome = { runtime: { sendMessage: h.send } };
  const { authenticatedFetch } = await import("../scripts/authClient.js");
  const gate = deferred();
  h.setToken((user, force) => user === h.a && force ? gate.promise : `fixture-${user.uid}`);
  let fetches = 0;
  const pending = authenticatedFetch(async () => { fetches++; return new Response("{}", { status: 401 }); },
    `${ORIGIN}/recommend`, {}, { apiBaseUrl: ORIGIN });
  const rejected = assert.rejects(pending, /계정이 변경/);
  await settle();
  h.responses.push(me("B", "approved", false));
  h.switchTo(h.b);
  await settle();
  const b = h.credential({ forceRefresh: true });
  gate.resolve("fixture-admin-A-refreshed");
  await rejected;
  assert.equal((await b).credential.headers.Authorization, "Bearer fixture-B");
  assert.equal(fetches, 1);
});

test("repair: a forced token refresh spanning approval generations is never shared", async () => {
  const h = repairHarness();
  await h.login();
  const gate = deferred();
  let forced = 0;
  h.setToken((_user, force) => force ? (++forced === 1 ? gate.promise : "new-generation-token") : "fixture-A");
  const old = h.credential({ forceRefresh: true });
  const rejected = assert.rejects(old, /계정이 변경/);
  await settle();
  h.responses.push(me("A", "blocked", false));
  await h.send({ type: "AUTH_REFRESH_ACCESS" });
  h.responses.push(me());
  await h.send({ type: "AUTH_REFRESH_ACCESS" });
  const next = h.credential({ forceRefresh: true });
  gate.resolve("old-generation-token");
  await rejected;
  assert.equal((await next).credential.headers.Authorization, "Bearer new-generation-token");
});

test("repair: late A denial and cold-worker denial cannot revoke B or a restored session", async () => {
  const h = repairHarness();
  await h.login();
  const generation = h.manager.state().sessionGeneration;
  h.responses.push(me("B", "approved", false));
  h.switchTo(h.b);
  await settle();
  const report = await h.send({ type: "AUTH_REPORT_DENIAL", purpose: "feature", code: "access_not_approved",
    accessStatus: "blocked", apiOrigin: ORIGIN, sessionGeneration: generation });
  assert.equal(report.applied, false);
  assert.equal(h.manager.state().account.uid, "B");
  assert.equal(h.manager.state().access.status, "approved");
  const cold = repairHarness({ restored: true });
  const stale = await cold.send({ type: "AUTH_REPORT_DENIAL", purpose: "feature", code: "access_not_approved",
    apiOrigin: ORIGIN, sessionGeneration: generation });
  assert.equal(stale.applied, false);
  assert.equal(cold.fetchCalls.length, 0, "a report must not restore an unbound cold session");
});

test("repair: denial bridge rejects wrong sender, purpose, code and origin", async () => {
  const h = repairHarness();
  await h.login();
  const base = { type: "AUTH_REPORT_DENIAL", purpose: "feature", code: "access_not_approved",
    apiOrigin: ORIGIN, sessionGeneration: h.manager.state().sessionGeneration };
  await assert.rejects(h.send(base, { url: "https://evil.example" }));
  await assert.rejects(h.send(base, { url: "chrome-extension://test/sidepanel.html", id: "foreign-extension" }));
  await assert.rejects(h.send({ ...base, purpose: "admin", code: "admin_required" }, { url: "chrome-extension://test/offscreen.html" }));
  await assert.rejects(h.send({ ...base, purpose: "access" }));
  await assert.rejects(h.send({ ...base, code: "admin_required" }));
  await assert.rejects(h.send({ ...base, apiOrigin: "https://evil.example" }));
  await assert.rejects(h.send({ ...base, apiOrigin: `${ORIGIN}/auth/me` }));
  assert.equal((await h.send({ ...base, apiOrigin: "http://localhost:8000" })).applied, false);
  assert.equal((await h.send({ ...base, sessionGeneration: null })).applied, false);
  assert.equal(h.manager.state().access.status, "approved");
  assert.equal(h.manager.state().access.canManage, true);
});

for (const purpose of ["feature", "admin"]) {
  for (const failure of [429, 503, "network"]) {
    test(`repair: ${purpose} denial survives /auth/me ${failure} without Firebase logout`, async () => {
      const h = repairHarness();
      await h.login();
      h.responses.push(failure === "network" ? () => { throw new TypeError("fixture network failure"); } : response(failure, {}));
      const old = h.manager.state();
      const result = await h.send({ type: "AUTH_REPORT_DENIAL", purpose,
        code: purpose === "feature" ? "access_not_approved" : "admin_required",
        accessStatus: "blocked", apiOrigin: ORIGIN, sessionGeneration: old.sessionGeneration });
      assert.equal(result.applied, true);
      await settle();
      const next = h.manager.state();
      assert.equal(next.status, "signed_in");
      assert.equal(h.auth.currentUser, h.a);
      assert.equal(h.signouts(), 0);
      if (purpose === "feature") {
        assert.equal(next.access.status, "blocked");
        assert.notEqual(next.sessionGeneration, old.sessionGeneration);
      } else {
        assert.equal(next.access.canManage, false);
        assert.equal(next.access.status, "approved", "admin denial preserves feature approval");
        assert.equal(next.sessionGeneration, old.sessionGeneration);
      }
      await assert.rejects(h.credential({ purpose }));
    });
  }
}

for (const operation of ["AUTH_REFRESH_ACCESS", "AUTH_REQUEST_ACCESS"]) {
  for (const boundary of ["account", "logout", "server"]) {
    test(`repair boundary: late ${operation} after ${boundary} cannot publish old permissions`, async () => {
      const h = repairHarness();
      await h.login();
      const gate = deferred();
      h.responses.push(() => gate.promise);
      const pending = h.send({ type: operation });
      await settle();
      if (boundary === "account") {
        h.responses.push(me("B", "pending", false));
        h.switchTo(h.b);
        await settle();
      } else if (boundary === "logout") await h.send({ type: "AUTH_SIGN_OUT" });
      else {
        h.responses.push(response(200, { mode: "legacy" }));
        await h.send({ type: "AUTH_CONFIGURE", apiBaseUrl: "http://localhost:8000" });
      }
      const before = JSON.stringify(h.manager.state());
      gate.resolve(operation === "AUTH_REFRESH_ACCESS" ? me() : response(200, { access_status: "approved" }));
      await pending;
      assert.equal(JSON.stringify(h.manager.state()), before);
    });
  }
}

test("repair boundary: an old observer cannot overwrite a newer manual admin revocation", async () => {
  const h = repairHarness();
  await h.login();
  const gate = deferred();
  h.responses.push(() => gate.promise);
  h.observe();
  await settle();
  h.responses.push(me("A", "approved", false));
  await h.send({ type: "AUTH_REFRESH_ACCESS" });
  gate.resolve(me());
  await settle();
  assert.equal(h.manager.state().access.canManage, false);
});

test("repair boundary: observer changes during interactive A login validate B separately", async () => {
  const h = repairHarness();
  const gate = deferred();
  h.responses.push(response(200, { mode: "firebase", firebase_project_id: "gen-lang-client-0392647514" }), () => gate.promise);
  const login = h.send({ type: "AUTH_SIGN_IN" });
  await settle();
  h.responses.push(me("B", "pending", false));
  h.switchTo(h.b);
  gate.resolve(response(403, { detail: { code: "auth_identity_unverified" } }));
  await login;
  await settle();
  assert.equal(h.manager.state().status, "signed_in");
  assert.equal(h.manager.state().account.uid, "B");
  assert.equal(h.manager.state().access.status, "pending");
  assert.equal(h.signouts(), 0);
  await assert.rejects(h.credential());
  assert.equal((await h.credential({ purpose: "access" })).credential.headers.Authorization, "Bearer fixture-B");
});

test("repair boundary: a new login waits for the preceding asynchronous SDK signout", async () => {
  const h = repairHarness();
  await h.login();
  const gate = deferred();
  const original = h.sdk.signOut;
  h.sdk.signOut = async () => { await gate.promise; await original(); };
  const logout = h.send({ type: "AUTH_SIGN_OUT" });
  h.responses.push(me());
  const login = h.send({ type: "AUTH_SIGN_IN" });
  await settle();
  gate.resolve();
  await Promise.all([logout, login]);
  assert.equal(h.manager.state().status, "signed_in");
  assert.equal(h.auth.currentUser, h.a);
});

test("repair boundary: SDK account callbacks after legacy server switch preserve legacy state", async () => {
  const h = repairHarness();
  await h.login();
  h.responses.push(response(200, { mode: "legacy" }));
  await h.send({ type: "AUTH_CONFIGURE", apiBaseUrl: "http://localhost:8000" });
  h.switchTo(h.b);
  assert.equal(h.manager.state().status, "legacy");
  const credential = await h.send({ type: "AUTH_GET_CREDENTIAL", apiBaseUrl: "http://localhost:8000" });
  assert.equal(credential.credential.mode, "legacy");
});

test("repair boundary: explicit admin denial discards old approved query even if its followup fails", async () => {
  const h = repairHarness();
  await h.login();
  const gate = deferred();
  h.responses.push(() => gate.promise);
  const pending = h.send({ type: "AUTH_REFRESH_ACCESS" });
  await settle();
  h.responses.push(response(503, {}));
  await h.send({ type: "AUTH_REPORT_DENIAL", purpose: "admin", code: "admin_required",
    apiOrigin: ORIGIN, sessionGeneration: h.manager.state().sessionGeneration });
  await settle();
  gate.resolve(me());
  await pending;
  assert.equal(h.manager.state().access.canManage, false);
  assert.equal(h.manager.state().access.status, "approved");
});

test("repair boundary: cold worker restores unapproved identity, rejects old denial generation, then rechecks approval", async () => {
  const h = repairHarness({ restored: true });
  h.responses.push(response(200, { mode: "firebase", firebase_project_id: "gen-lang-client-0392647514" }), me("A", "pending", false), me("A", "pending", false));
  await h.send({ type: "AUTH_REFRESH_ACCESS" });
  assert.equal(h.manager.state().status, "signed_in");
  assert.equal(h.manager.state().access.status, "pending");
  const stale = await h.send({ type: "AUTH_REPORT_DENIAL", purpose: "feature", code: "access_not_approved",
    apiOrigin: ORIGIN, sessionGeneration: "old-worker:100" });
  assert.equal(stale.applied, false);
  h.responses.push(me("A", "approved", false));
  await h.send({ type: "AUTH_REFRESH_ACCESS" });
  assert.equal(h.manager.state().access.status, "approved");
  assert.equal(h.authCalls.length, 0, "restoration never opens Google consent");
});

test("repair boundary: offscreen feature denial is accepted and its failed recheck keeps features closed", async () => {
  const h = repairHarness();
  await h.login();
  h.responses.push(response(429, {}));
  const result = await h.send({ type: "AUTH_REPORT_DENIAL", purpose: "feature", code: "access_not_approved",
    accessStatus: "approved", apiOrigin: ORIGIN, sessionGeneration: h.manager.state().sessionGeneration },
  { url: "chrome-extension://test/offscreen.html" });
  assert.equal(result.applied, true);
  await settle();
  assert.equal(h.manager.state().access.status, "unavailable", "a denial cannot supply approved status");
  await assert.rejects(h.credential());
});

test("repair boundary: timed-out SDK signout retains ownership until the actual write finishes", async () => {
  const h = repairHarness({ timeout: 30 });
  await h.login();
  const gate = deferred();
  const original = h.sdk.signOut;
  h.sdk.signOut = async () => { await gate.promise; await original(); };
  await assert.rejects(h.send({ type: "AUTH_SIGN_OUT" }), /시간이 초과/);
  h.responses.push(me());
  const login = h.send({ type: "AUTH_SIGN_IN" });
  await settle();
  const callsWhileOldWritePending = h.authCalls.length;
  gate.resolve();
  await login;
  assert.equal(callsWhileOldWritePending, 1, "Google consent must wait for the previous SDK write");
  assert.equal(h.manager.state().status, "signed_in");
});

test("repair boundary: observer network failure after explicit denial preserves blocked identity and drops old approval", async () => {
  const h = repairHarness();
  await h.login();
  const old = deferred();
  h.responses.push(() => old.promise);
  await h.send({ type: "AUTH_REPORT_DENIAL", purpose: "feature", code: "access_not_approved",
    accessStatus: "blocked", apiOrigin: ORIGIN, sessionGeneration: h.manager.state().sessionGeneration });
  await settle();
  h.responses.push(() => { throw new TypeError("fixture observer network failure"); });
  h.observe();
  await settle();
  const afterFailure = h.manager.state();
  const denialGeneration = afterFailure.sessionGeneration;
  old.resolve(me());
  await settle();
  assert.equal(afterFailure.status, "signed_in");
  assert.equal(afterFailure.account.uid, "A");
  assert.equal(afterFailure.access.status, "blocked");
  assert.match(afterFailure.access.error, /network failure/);
  assert.equal(h.manager.state().sessionGeneration, denialGeneration);
  assert.equal(h.manager.state().access.status, "blocked");
  assert.equal(h.auth.currentUser, h.a);
  assert.equal(h.signouts(), 0);
  await assert.rejects(h.credential());
  assert.equal((await h.credential({ purpose: "access" })).credential.headers.Authorization, "Bearer fixture-A");
});

test("repair final boundary: login wait stays bounded while the SDK signout retains ownership", async () => {
  const h = repairHarness({ timeout: 30 });
  await h.login();
  const gate = deferred();
  const original = h.sdk.signOut;
  let writes = 0;
  h.sdk.signOut = async () => { writes++; await gate.promise; await original(); };
  await assert.rejects(h.send({ type: "AUTH_SIGN_OUT" }), /시간이 초과/);
  h.responses.push(me());
  const login = h.send({ type: "AUTH_SIGN_IN" });
  let timer;
  let outcome, consentCalls, pendingWrites;
  try {
    outcome = await Promise.race([login, new Promise((resolve) => {
      timer = setTimeout(() => resolve("unbounded wait"), 250);
    })]);
    consentCalls = h.authCalls.length;
    pendingWrites = writes;
  } finally {
    clearTimeout(timer);
    gate.resolve();
    await login;
    await settle();
  }
  assert.equal(outcome.state?.status, "signed_out", "login must finish within its bounded waits");
  assert.equal(consentCalls, 1, "pending signout must prevent Google consent");
  assert.equal(pendingWrites, 1, "timeout must not start another SDK signout");
  await h.send({ type: "AUTH_SIGN_IN" });
  assert.equal(h.manager.state().status, "signed_in");
  assert.equal(h.auth.currentUser, h.a);
});

test("repair final boundary: SDK signout rejection releases ownership for the next login", async () => {
  const h = repairHarness();
  await h.login();
  const gate = deferred();
  h.sdk.signOut = () => gate.promise;
  const logout = assert.rejects(h.send({ type: "AUTH_SIGN_OUT" }), /fixture SDK rejection/);
  await settle();
  gate.reject(new Error("fixture SDK rejection"));
  await logout;
  h.responses.push(me());
  await h.send({ type: "AUTH_SIGN_IN" });
  assert.equal(h.authCalls.length, 2);
  assert.equal(h.manager.state().status, "signed_in");
});

for (const [purpose, failure] of [["feature", 429], ["feature", 503], ["feature", "timeout"], ["admin", "network"]]) {
  test(`repair final boundary: ${purpose} denial survives observer ${failure} without restoring old approval`, async () => {
    const h = repairHarness(failure === "timeout" ? { timeout: 30 } : {});
    await h.login();
    const old = deferred();
    h.responses.push(() => old.promise);
    await h.send({ type: "AUTH_REPORT_DENIAL", purpose,
      code: purpose === "feature" ? "access_not_approved" : "admin_required",
      accessStatus: "blocked", apiOrigin: ORIGIN, sessionGeneration: h.manager.state().sessionGeneration });
    await settle();
    const denied = h.manager.state();
    const timedOut = deferred();
    h.responses.push(failure === "network" ? () => { throw new TypeError("fixture observer network failure"); }
      : failure === "timeout" ? () => timedOut.promise : response(failure, {}));
    h.observe();
    if (failure === "timeout") await new Promise((resolve) => setTimeout(resolve, 60));
    await settle();
    const afterFailure = h.manager.state();
    old.resolve(me());
    timedOut.resolve(me());
    await settle();
    const final = h.manager.state();
    assert.equal(afterFailure.status, "signed_in");
    assert.equal(afterFailure.account.uid, "A");
    assert.equal(final.sessionGeneration, denied.sessionGeneration);
    assert.equal(final.access.status, denied.access.status);
    assert.equal(final.access.canManage, denied.access.canManage);
    assert.ok(final.access.error);
    assert.equal(h.auth.currentUser, h.a);
    assert.equal(h.signouts(), 0);
    await assert.rejects(h.credential({ purpose }));
    assert.equal((await h.credential({ purpose: "access" })).credential.headers.Authorization, "Bearer fixture-A");
  });
}

test("repair final boundary: observer network failure on new B never preserves A identity or permissions", async () => {
  const h = repairHarness();
  await h.login();
  h.responses.push(() => { throw new TypeError("fixture B network failure"); });
  h.switchTo(h.b);
  await settle();
  assert.equal(h.manager.state().status, "error");
  assert.equal(h.manager.state().account, null);
  assert.equal(h.manager.state().access, null);
  await assert.rejects(h.credential({ purpose: "access" }));
});

for (const status of [401, 403]) {
  test(`repair final boundary: observer HTTP ${status} clears previously verified identity after denial`, async () => {
    const h = repairHarness();
    await h.login();
    h.responses.push(response(503, {}));
    await h.send({ type: "AUTH_REPORT_DENIAL", purpose: "feature", code: "access_not_approved",
      accessStatus: "blocked", apiOrigin: ORIGIN, sessionGeneration: h.manager.state().sessionGeneration });
    await settle();
    h.responses.push(response(status, { detail: { code: status === 403 ? "auth_identity_unverified" : "auth_unauthorized" } }));
    h.observe();
    await settle();
    assert.equal(h.manager.state().status, status === 403 ? "denied" : "error");
    assert.equal(h.manager.state().account, null);
    assert.equal(h.manager.state().access, null);
    await assert.rejects(h.credential({ purpose: "access" }));
  });
}
