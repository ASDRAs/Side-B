const { expect, test } = require("@playwright/test");
const { launchExtensionPage } = require("./extension.cjs");

async function installAuthFixture(worker, { blank = false } = {}) {
  await worker.evaluate(({ blank }) => {
    let observer;
    const auth = { currentUser: null };
    const user = { uid: "fixture-user", email: "listener@example.com", displayName: "Listener" };
    globalThis.authFixture = { scopes: [], user, meStatus: 200, pendingGoogle: false };
    const manager = SideBAuthBundle.createAuthManager({
      chromeApi: {
        runtime: chrome.runtime,
        identity: { getAuthToken: async (details) => {
          authFixture.scopes.push(details.scopes);
          if (authFixture.pendingGoogle) await new Promise((resolve) => { authFixture.releaseGoogle = resolve; });
          return { token: "google-fixture" };
        } },
      },
      config: { ...SideBAuthConfig, firebase: blank ? {} : {
        apiKey: "public-fixture", authDomain: "fixture.firebaseapp.com", projectId: "fixture", appId: "fixture",
      } },
      sdk: {
        initializeApp: () => ({}), initializeAuth: () => auth, indexedDBLocalPersistence: {},
        GoogleAuthProvider: { credential: () => ({}) },
        onIdTokenChanged: (_auth, callback) => { observer = callback; queueMicrotask(() => callback(auth.currentUser)); },
        signInWithCredential: async () => { auth.currentUser = user; observer(user); return { user }; },
        signOut: async () => { auth.currentUser = null; observer(null); },
        getIdToken: async () => "firebase-fixture-token",
      },
      fetchImpl: async (url) => {
        if (url.endsWith("/auth/config")) return new Response(JSON.stringify({ mode: "firebase", firebase_project_id: "fixture" }));
        if (url.endsWith("/auth/me")) return new Response(JSON.stringify(user), { status: authFixture.meStatus });
        throw new Error(`Unexpected auth transport ${url}`);
      },
    });
    // Replace only SDK/Google/backend-auth transports. The real manager, worker
    // message permissions, UI actions and feature HTTP adapters are exercised.
    Object.assign(authManager, manager);
  }, { blank });
}

const track = { name: "Hello", artist: "Adele" };
const recommendation = { track_name: track.name, artist: track.artist, top_n: 10,
  result: { similar: [track], reverse: [], hidden: [] } };
const matches = { requested: 1, deduplicated: 0, matched: [{ ...track, position: 0,
  video_id: "fixtureVideo", youtube_title: "Adele - Hello", channel_title: "Adele", confidence: 0.95 }], unmatched: [] };

async function logIn(page) {
  await expect(page.locator("#authGateSignInButton")).toBeVisible();
  await page.locator("#authGateSignInButton").click();
  await expect(page.locator("#authStatus")).toHaveText("로그인됨");
  await expect(page.locator("#authGate")).toBeHidden();
}

test("intro yields to the sign-in gate and reduced motion skips it", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo, {
    setupWorker: installAuthFixture,
    showIntro: true,
  });
  try {
    await page.setViewportSize({ width: 280, height: 760 });
    await expect(page.locator("#introOverlay")).toBeVisible();
    await expect(page.locator("#introOverlay")).toBeHidden({ timeout: 4_000 });
    await expect(page.locator("#authGate")).toBeVisible();
    await expect(page.locator(".search-bar")).toBeHidden();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("sign-in-gate-280.png"), fullPage: true });

    const reduced = await context.newPage();
    await reduced.emulateMedia({ reducedMotion: "reduce" });
    await reduced.goto(page.url());
    await expect(reduced.locator("#introOverlay")).toBeHidden();
    await expect(reduced.locator("#authGate")).toBeVisible();
    await reduced.close();
  } finally { await context.close(); }
});

test("managed login replaces token input, sends bearer to recommendation/matches, and logout clears both panels", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo, { setupWorker: installAuthFixture });
  const [worker] = context.serviceWorkers();
  try {
    await expect(page.locator("#legacyAuthSettings")).toBeHidden();
    await logIn(page);
    await page.locator("#settingsToggle").click();
    for (const width of [280, 480]) {
      for (const colorScheme of ["light", "dark"]) {
        await page.setViewportSize({ width, height: 900 });
        await page.emulateMedia({ colorScheme });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: testInfo.outputPath(`signed-in-${width}-${colorScheme}.png`), fullPage: true });
      }
    }
    await page.locator("#settingsToggle").click();
    const seen = [];
    await page.route("**/recommend", (route) => {
      seen.push(route.request().headers());
      return route.fulfill({ json: recommendation });
    });
    await page.route("**/exports/youtube/matches", (route) => {
      seen.push(route.request().headers());
      return route.fulfill({ json: matches });
    });
    await page.route("**/preview/**", (route) => route.fulfill({ status: 404, body: "" }));
    await page.locator("#query").fill("Adele - Hello");
    await page.locator("#submitButton").click();
    await expect(page.locator(".track-item")).toHaveCount(1);
    await page.locator(".export-button").click();
    await expect(page.locator("#youtubeMatchReview")).toBeVisible();
    expect(seen).toHaveLength(2);
    for (const headers of seen) {
      expect(headers.authorization).toBe("Bearer firebase-fixture-token");
      expect(headers["x-side-b-access-token"]).toBeUndefined();
      expect(headers["x-side-b-export-token"]).toBeUndefined();
    }
    const other = await context.newPage();
    await other.goto(page.url());
    await other.locator("#settingsToggle").click();
    await expect(other.locator("#accountLabel")).toContainText("listener@example.com");
    await other.locator("#signOutButton").click();
    await expect(page.locator("#youtubeMatchReview")).toBeHidden();
    await expect(page.locator(".track-item")).toHaveCount(0);
    await expect(other.locator("#authStatus")).toContainText("로그인되지 않음");
    expect(await worker.evaluate(() => authFixture.scopes)).toEqual([["openid", "email", "profile"]]);
    const stored = await worker.evaluate(() => chrome.storage.local.get(null));
    expect(JSON.stringify(stored)).not.toContain("firebase-fixture-token");
    expect(stored.backendAccessToken).toBeFalsy();
  } finally { await context.close(); }
});

test("logout during pending consent cannot restore the account", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo, { setupWorker: installAuthFixture });
  const [worker] = context.serviceWorkers();
  try {
    await worker.evaluate(() => { authFixture.pendingGoogle = true; });
    await page.locator("#authGateSignInButton").click();
    await expect(page.locator("#authGateStatus")).toHaveText("Google 로그인 중");
    await page.locator("#authGateSignOutButton").click();
    await worker.evaluate(() => authFixture.releaseGoogle());
    await expect(page.locator("#authStatus")).toHaveText("로그인되지 않음");
    expect(await worker.evaluate(() => authManager.state().account)).toBeNull();
  } finally { await context.close(); }
});

test("logout aborts a pending recommendation and prevents its late results", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo, { setupWorker: installAuthFixture });
  let held;
  try {
    await logIn(page);
    await page.route("**/recommend", (route) => { held = route; });
    await page.locator("#query").fill("Adele - Hello");
    await page.locator("#submitButton").click();
    await expect.poll(() => Boolean(held)).toBe(true);
    await page.locator("#settingsToggle").click();
    await page.locator("#signOutButton").click();
    await held.fulfill({ json: recommendation }).catch(() => {});
    await expect(page.locator("#submitButton")).not.toHaveText("취소");
    await expect(page.locator(".track-item")).toHaveCount(0);
  } finally { await context.close(); }
});

test("managed EQ uses bearer and logout stops capture and invalidates presets", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo, { setupWorker: installAuthFixture });
  const [worker] = context.serviceWorkers();
  try {
    await logIn(page);
    await context.route("https://music.youtube.com/**", (route) => route.fulfill({
      contentType: "text/html", body: '<script>navigator.mediaSession.metadata=new MediaMetadata({title:"Hello",artist:"Adele"})</script>',
    }));
    const music = await context.newPage();
    await music.goto("https://music.youtube.com/watch?v=fixture");
    const tabId = await worker.evaluate(async () => (await chrome.tabs.query({ url: "https://music.youtube.com/*" }))[0].id);
    let hold = false;
    let pendingAnalysis;
    await context.route("**/genre-classification", (route) => {
      expect(route.request().headers().authorization).toBe("Bearer firebase-fixture-token");
      expect(route.request().headers()["x-side-b-access-token"]).toBeUndefined();
      if (hold) { pendingAnalysis = route; return; }
      return route.fulfill({ json: { genre: "dance", score: 0.3, model_version: "fixture" } });
    });
    const audio = await context.newPage();
    await audio.goto(new URL("offscreen.html", page.url()).href);
    await audio.evaluate(async (tabId) => {
      globalThis.inputContext = new AudioContext();
      const output = inputContext.createMediaStreamDestination();
      createTabMediaStream = async () => output.stream;
      await startEq({ streamId: "fixture", tabId, mode: "auto" });
    }, tabId);
    await expect.poll(() => audio.evaluate(() => getState().status)).toBe("applied");
    hold = true;
    await audio.evaluate(() => setEqMode("auto"));
    await expect.poll(() => Boolean(pendingAnalysis)).toBe(true);
    await page.locator("#settingsToggle").click();
    await page.locator("#signOutButton").click();
    await expect.poll(() => audio.evaluate(() => getState().capturing)).toBe(false);
    await pendingAnalysis.fulfill({ json: { genre: "rock_metal", score: 0.8, model_version: "late" } }).catch(() => {});
    expect(await audio.evaluate(() => filterNodes.length)).toBe(0);
    await audio.evaluate(() => inputContext.close());
  } finally { await context.close(); }
});

test("logout during matching cancels its HTTP request and never opens a stale review", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo, { setupWorker: installAuthFixture });
  let held;
  try {
    await logIn(page);
    await page.route("**/recommend", (route) => route.fulfill({ json: recommendation }));
    await page.route("**/preview/**", (route) => route.fulfill({ status: 404, body: "" }));
    await page.route("**/exports/youtube/matches", (route) => { held = route; });
    await page.locator("#query").fill("Adele - Hello");
    await page.locator("#submitButton").click();
    await page.locator(".export-button").click();
    await expect.poll(() => Boolean(held)).toBe(true);
    await page.locator("#settingsToggle").click();
    await page.locator("#signOutButton").click();
    await held.fulfill({ json: matches }).catch(() => {});
    await expect(page.locator("#youtubeMatchReview")).toBeHidden();
    await expect(page.locator("#youtubeExportPanel")).toBeHidden();
    await expect(page.locator(".track-item")).toHaveCount(0);
  } finally { await context.close(); }
});

test("missing Firebase setup fails visibly instead of requesting a team token", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo, { setupWorker: (worker) => installAuthFixture(worker, { blank: true }) });
  try {
    await expect(page.locator("#authGateStatus")).toContainText("로그인 설정을 사용할 수 없음");
    await expect(page.locator("#authGateSignInButton")).toBeDisabled();
    await expect(page.locator("#legacyAuthSettings")).toBeHidden();
    await page.locator("#authGateSettingsButton").click();
    await expect(page.locator("#settingsPanel")).toBeVisible();
    await page.locator("#settingsBack").click();
    await expect(page.locator("#authGate")).toBeVisible();
    for (const width of [280, 480]) {
      for (const colorScheme of ["light", "dark"]) {
        await page.setViewportSize({ width, height: 900 });
        await page.emulateMedia({ colorScheme });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: testInfo.outputPath(`auth-${width}-${colorScheme}.png`), fullPage: true });
      }
    }
  } finally { await context.close(); }
});

test("reopening restores only the signed-in account's persisted playlist result", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo, { setupWorker: installAuthFixture });
  const [worker] = context.serviceWorkers();
  try {
    await logIn(page);
    for (const [ownerUid, visible] of [["fixture-user", true], ["another-user", false]]) {
      await worker.evaluate((ownerUid) => chrome.storage.local.set({ youtubeExport: {
        ownerUid, operationId: "persisted", status: "completed", title: "Owned playlist",
        added: 1, requested: 1, matched: 1, toAdd: 1, failed: [],
        youtubeMusicUrl: "https://music.youtube.com/playlist?list=fixture",
      } }), ownerUid);
      const reopened = await context.newPage();
      await reopened.goto(page.url());
      await expect(reopened.locator("#authStatus")).toHaveText("로그인됨");
      if (visible) await expect(reopened.locator("#youtubeExportPanel")).toBeVisible();
      else await expect(reopened.locator("#youtubeExportPanel")).toBeHidden();
      await reopened.close();
    }
  } finally { await context.close(); }
});
