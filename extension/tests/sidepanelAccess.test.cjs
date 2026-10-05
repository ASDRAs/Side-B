// Drives the real sidepanel.js module against a minimal fake DOM and a fake
// background worker. It covers panel state logic only; real Chrome rendering
// and the Playwright E2E suite are separate (and were not runnable here).
const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");
const { pathToFileURL } = require("node:url");

const ORIGIN = "https://side-b-backend-1073342688292.asia-northeast3.run.app";
const settle = async (turns = 12) => { for (let i = 0; i < turns; i++) await new Promise(setImmediate); };

class FakeClassList {
  constructor() { this.values = new Set(); }
  add(...names) { names.forEach((name) => this.values.add(name)); }
  remove(...names) { names.forEach((name) => this.values.delete(name)); }
  toggle(name, force) {
    const on = force === undefined ? !this.values.has(name) : Boolean(force);
    if (on) this.values.add(name); else this.values.delete(name);
    return on;
  }
  contains(name) { return this.values.has(name); }
}

class FakeElement {
  constructor(tag = "div", selector = "") {
    this.tagName = tag.toUpperCase();
    this.selector = selector;
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.checked = false;
    this.open = false;
    this.paused = true;
    this.ended = false;
    this.title = "";
    this.type = "";
    this.className = "";
    this.offsetWidth = 0;
    this.isConnected = true;
    this.noValidate = false;
    this.tabIndex = 0;
    this.dataset = {};
    this.style = {};
    this.classList = new FakeClassList();
    this.attributes = new Map();
    this.listeners = new Map();
    this.children = [];
    this.queries = new Map();
    this.ownText = "";
    this.content = { cloneNode: () => new FakeElement("fragment") };
  }
  get textContent() { return this.ownText + this.children.map((child) => child.textContent ?? String(child)).join(""); }
  set textContent(value) { this.ownText = String(value ?? ""); this.children = []; }
  get innerHTML() { return ""; }
  set innerHTML(_value) { throw new Error("innerHTML must not be used for external strings"); }
  get src() { return this.attributes.get("src") || ""; }
  set src(value) { this.attributes.set("src", String(value)); }
  get href() { return this.attributes.get("href") || ""; }
  set href(value) { this.attributes.set("href", String(value)); }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  removeEventListener() {}
  dispatch(type, event = {}) {
    const payload = { target: this, preventDefault() {}, ...event };
    return Promise.all((this.listeners.get(type) || []).map((listener) => listener(payload)));
  }
  click() { return this.dispatch("click"); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  removeAttribute(name) { this.attributes.delete(name); }
  replaceChildren(...nodes) { this.children = nodes; this.ownText = ""; }
  append(...nodes) { this.children.push(...nodes); }
  remove() { this.isConnected = false; }
  querySelector(selector) {
    if (!this.queries.has(selector)) this.queries.set(selector, new FakeElement("div", selector));
    return this.queries.get(selector);
  }
  querySelectorAll(selector) {
    // The playlist picker needs its two mode radios, as in sidepanel.html.
    if (selector !== '[name="playlistDestinationMode"]') return [];
    if (!this.queries.has(selector)) {
      this.queries.set(selector, ["create", "append"].map((value) => Object.assign(
        new FakeElement("input"), { type: "radio", value, checked: value === "create" })));
    }
    return this.queries.get(selector);
  }
  closest() { return new FakeElement(); }
  focus() {}
  select() {}
  scrollIntoView() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
  load() {}
  pause() { this.paused = true; }
  play() { this.paused = false; return Promise.resolve(); }
}

const allText = (element) => element.textContent;
const findButton = (element, text) => {
  const stack = [...element.children];
  while (stack.length) {
    const node = stack.shift();
    if (node instanceof FakeElement) {
      if (node.tagName === "BUTTON" && node.textContent.startsWith(text)) return node;
      stack.push(...node.children);
    }
  }
  return null;
};

let loads = 0;

async function loadPanel({ state, onMessage = () => undefined, fetchImpl }) {
  const elements = new Map();
  const runtimeListeners = [];
  const sent = [];
  const fetches = [];
  const revoked = [];
  let current = state;
  const document = {
    body: new FakeElement("body"),
    visibilityState: "visible",
    hidden: false,
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, new FakeElement("div", selector));
      return elements.get(selector);
    },
    querySelectorAll() { return []; },
    createElement(tag) { return new FakeElement(tag); },
    addEventListener() {},
  };
  const credentialFor = (purpose) => {
    const allowed = purpose === "access" || (purpose === "admin" && current.access?.canManage) ||
      ((purpose ?? "feature") === "feature" && current.access?.status === "approved");
    if (!allowed) return { ok: false, error: "관리자 승인 후 사용할 수 있습니다." };
    return { ok: true, credential: { mode: "firebase", sessionGeneration: current.sessionGeneration,
      headers: { Authorization: `Bearer token-${purpose ?? "feature"}` } } };
  };
  globalThis.document = document;
  globalThis.window = {
    matchMedia: () => ({ matches: true }),
    setTimeout: (fn, ms) => { const timer = setTimeout(fn, ms); timer.unref?.(); return timer; },
    clearTimeout,
  };
  globalThis.setInterval = () => 0;
  globalThis.chrome = {
    runtime: {
      onMessage: { addListener(listener) { runtimeListeners.push(listener); } },
      async sendMessage(message) {
        sent.push(message);
        const custom = await onMessage(message, { setState: (next) => { current = next; } });
        if (custom !== undefined) return custom;
        switch (message.type) {
          case "AUTH_CONFIGURE": return { ok: true, state: current };
          case "AUTH_GET_CREDENTIAL": return credentialFor(message.purpose);
          case "AUTH_SIGN_OUT": current = { ...current, status: "signed_out", access: null, sessionGeneration: "out" };
            return { ok: true, state: current };
          case "GET_EQ_STATE":
          case "STOP_EQ": return { ok: true, active: false, status: "inactive" };
          case "GET_YOUTUBE_EXPORT_STATE": return { ok: true, state: null };
          default: return { ok: true, state: current };
        }
      },
    },
    storage: {
      local: { get: async () => ({ apiBaseUrl: ORIGIN, apiBaseUrlStorageVersion: 3 }), set: async () => {}, remove: async () => {} },
      onChanged: { addListener() {} },
    },
    windows: { getCurrent: async () => ({ id: 1 }) },
  };
  globalThis.fetch = async (url, init) => {
    fetches.push({ url: String(url), init });
    return fetchImpl(String(url), init);
  };
  const originalRevoke = URL.revokeObjectURL;
  URL.revokeObjectURL = (url) => { revoked.push(url); return originalRevoke(url); };
  const module = pathToFileURL(path.join(__dirname, "..", "sidepanel.js")).href;
  await import(`${module}?panel=${++loads}`);
  await settle();
  const $ = (selector) => document.querySelector(selector);
  return {
    $, document, sent, fetches, revoked,
    setState(next) { current = next; },
    push(next) {
      current = next;
      for (const listener of runtimeListeners) listener({ target: "auth-ui", type: "AUTH_STATE_CHANGED", state: next });
    },
    types: () => sent.map((message) => message.type),
  };
}

const account = { uid: "user-a", email: "a@example.com", displayName: "A" };
const session = (access, extra = {}) => ({
  mode: "firebase", status: "signed_in", apiOrigin: ORIGIN, account, error: null,
  sessionGeneration: "g1",
  access: access && { status: access, canManage: false, requestedAt: "2026-10-05T01:02:03Z", store: "firestore", error: null, ...extra },
});
const json = (body, status = 200) => new Response(JSON.stringify(body), { status });

test("pending accounts stay signed in on the access screen with refresh and logout only", async () => {
  const panel = await loadPanel({ state: session("pending"), fetchImpl: () => assert.fail("no feature request") });
  assert.equal(panel.document.body.classList.contains("auth-gated"), true);
  assert.equal(panel.$("#authGate").hidden, false);
  assert.equal(panel.$("#accessPanel").hidden, false);
  assert.equal(panel.$("#authGateSignInButton").hidden, true);
  assert.equal(panel.$("#accessRequestButton").hidden, true);
  assert.equal(panel.$("#authGateTitle").textContent, "관리자 승인 대기 중");
  assert.equal(panel.$("#accessServer").textContent, ORIGIN);
  assert.match(panel.$("#accessAccount").textContent, /a@example\.com/);
  assert.equal(panel.$("#accessRequestedRow").hidden, false);
  assert.equal(panel.$("#adminSettings").hidden, true);
  assert.match(panel.$("#authStatus").textContent, /로그인됨 · 승인 대기/);
});

test("an unregistered account can request access and then sees the pending screen", async () => {
  const panel = await loadPanel({
    state: session("unregistered"),
    onMessage: (message, { setState }) => {
      if (message.type !== "AUTH_REQUEST_ACCESS") return undefined;
      const next = session("pending");
      setState(next);
      return { ok: true, state: next };
    },
    fetchImpl: () => assert.fail("no feature request"),
  });
  assert.equal(panel.$("#accessRequestButton").hidden, false);
  await panel.$("#accessRequestButton").click();
  await settle();
  assert.ok(panel.types().includes("AUTH_REQUEST_ACCESS"));
  assert.equal(panel.$("#accessRequestButton").hidden, true);
  assert.equal(panel.$("#authGateTitle").textContent, "관리자 승인 대기 중");
});

test("manual refresh after approval opens the feature UI in the same session", async () => {
  const panel = await loadPanel({
    state: session("pending"),
    onMessage: (message, { setState }) => {
      if (message.type !== "AUTH_REFRESH_ACCESS") return undefined;
      const next = session("approved");
      setState(next);
      return { ok: true, state: next };
    },
    fetchImpl: () => json({}),
  });
  await panel.$("#accessRefreshButton").click();
  await settle();
  assert.equal(panel.document.body.classList.contains("auth-gated"), false);
  assert.equal(panel.$("#authGate").hidden, true);
  assert.ok(!panel.types().includes("AUTH_SIGN_IN"));
});

test("a feature 403 reports scoped denial, clears results and stops EQ without logging out", async () => {
  let recommendCalls = 0;
  const panel = await loadPanel({
    state: session("approved"),
    onMessage: (message, { setState }) => {
      if (message.type !== "AUTH_REPORT_DENIAL") return undefined;
      const next = { ...session("blocked"), sessionGeneration: "g2" };
      setState(next);
      panel.push(next);
      return { ok: true, state: next };
    },
    fetchImpl: (url) => {
      if (url.endsWith("/recommend")) {
        recommendCalls++;
        return json({ detail: { code: "access_not_approved", access_status: "blocked", message: "차단" } }, 403);
      }
      return assert.fail(`unexpected ${url}`);
    },
  });
  const stopsBefore = panel.types().filter((type) => type === "STOP_EQ").length;
  panel.$("#query").value = "Seed";
  await panel.$("#recommendForm").dispatch("submit");
  await settle();
  assert.equal(recommendCalls, 1);
  assert.ok(panel.types().includes("AUTH_REPORT_DENIAL"));
  assert.ok(!panel.types().includes("AUTH_SIGN_OUT"));
  assert.equal(panel.$("#authGateTitle").textContent, "이용이 차단되었습니다");
  assert.ok(panel.types().filter((type) => type === "STOP_EQ").length > stopsBefore);
  assert.equal(panel.$("#seedSection").hidden, true);
});

test("gated sessions never send feature requests from the panel", async () => {
  const panel = await loadPanel({ state: session("pending"), fetchImpl: () => assert.fail("must not fetch") });
  panel.$("#query").value = "Seed";
  await panel.$("#recommendForm").dispatch("submit");
  await settle();
  assert.equal(panel.fetches.length, 0);
  assert.match(panel.$("#statusMessage").textContent, /승인 후/);
});

function adminFetch(items, decisions = []) {
  return (url, init) => {
    if (url.includes("/admin/access-users?")) return json({ status: "pending", items, next_cursor: null });
    if (url.includes("/decision")) {
      const next = decisions.shift();
      if (next instanceof Error) throw next;
      return next || json({ action: "approve", status: "approved" });
    }
    return assert.fail(`unexpected ${url}`);
  };
}

const pendingItem = {
  uid: "listener", status: "pending", revision: 1, is_admin: false,
  email: "<img src=x onerror=alert(1)>@example.com", display_name: "<b>Listener</b>",
  requested_at: "2026-10-05T00:00:00Z",
};

test("administrators see the server-flagged menu and external strings render as text", async () => {
  const panel = await loadPanel({
    state: session("approved", { canManage: true }),
    fetchImpl: adminFetch([pendingItem, { ...pendingItem, uid: "other-admin", is_admin: true }]),
  });
  assert.equal(panel.$("#adminSettings").hidden, false);
  panel.$("#settingsPanel").open = true;
  await panel.$("#settingsPanel").dispatch("toggle");
  await settle();
  const list = panel.fetches.find(({ url }) => url.includes("/admin/access-users?"));
  assert.equal(list.init.headers.get("Authorization"), "Bearer token-admin");
  assert.match(list.url, /status=pending&limit=25/);
  const rows = panel.$("#adminList").children;
  assert.equal(rows.length, 2);
  assert.match(allText(rows[0]), /<img src=x onerror=alert\(1\)>@example\.com/);
  assert.ok(findButton(rows[0], "승인"));
  assert.equal(findButton(rows[1], "승인"), null, "administrator rows have no actions");
});

test("decisions send revision and operation ID; a failed one retries with the same ID", async () => {
  const panel = await loadPanel({
    state: session("approved", { canManage: true }),
    fetchImpl: adminFetch([pendingItem], [new TypeError("offline"), json({ action: "approve", status: "approved" })]),
  });
  panel.$("#settingsPanel").open = true;
  await panel.$("#settingsPanel").dispatch("toggle");
  await settle();
  await findButton(panel.$("#adminList").children[0], "승인").click();
  await settle();
  assert.match(panel.$("#adminStatus").textContent, /다시 시도/);
  assert.equal(panel.$("#adminStatus").dataset.error, "true");
  await findButton(panel.$("#adminList").children[0], "승인").click();
  await settle();
  const decisions = panel.fetches.filter(({ url }) => url.includes("/decision"));
  assert.equal(decisions.length, 2);
  const bodies = decisions.map(({ init }) => JSON.parse(init.body));
  assert.deepEqual(Object.keys(bodies[0]).sort(), ["action", "expected_revision", "operation_id"]);
  assert.equal(bodies[0].expected_revision, 1);
  assert.equal(bodies[0].operation_id, bodies[1].operation_id);
  assert.match(panel.$("#adminStatus").textContent, /승인했습니다/);
});

test("a conflicting decision reloads the list instead of reporting success", async () => {
  const panel = await loadPanel({
    state: session("approved", { canManage: true }),
    fetchImpl: adminFetch([pendingItem], [json({ detail: { code: "access_revision_conflict", message: "x" } }, 409)]),
  });
  panel.$("#settingsPanel").open = true;
  await panel.$("#settingsPanel").dispatch("toggle");
  await settle();
  await findButton(panel.$("#adminList").children[0], "거절").click();
  await settle();
  assert.match(panel.$("#adminStatus").textContent, /이미 처리되었거나/);
  assert.equal(panel.fetches.filter(({ url }) => url.includes("/admin/access-users?")).length, 2);
});

test("account change, logout or worker restart discards the admin list and permissions", async () => {
  const panel = await loadPanel({
    state: session("approved", { canManage: true }),
    fetchImpl: adminFetch([pendingItem]),
  });
  panel.$("#settingsPanel").open = true;
  await panel.$("#settingsPanel").dispatch("toggle");
  await settle();
  assert.equal(panel.$("#adminList").children.length, 1);
  panel.push({ ...session("approved", { canManage: true }), sessionGeneration: "restarted-worker:0" });
  await settle();
  assert.equal(panel.$("#adminList").children.length, 0);
  assert.ok(panel.types().includes("STOP_EQ"));

  await panel.$("#settingsPanel").dispatch("toggle");
  await settle();
  assert.equal(panel.$("#adminList").children.length, 1);
  panel.push({ ...session("approved"), sessionGeneration: "restarted-worker:0" });
  await settle();
  assert.equal(panel.$("#adminSettings").hidden, true);
  assert.equal(panel.$("#adminList").children.length, 0);
});

test("previews play from an authenticated Blob and are revoked on reset", async () => {
  const panel = await loadPanel({
    state: session("approved"),
    fetchImpl: (url) => {
      if (url.endsWith("/recommend")) {
        return json({ track_name: "Seed", artist: "Artist", source_id: "deezer:42",
          result: { similar: [], reverse: [], hidden: [] } });
      }
      if (url.includes("/preview/stream?")) {
        return new Response(new Uint8Array(8), { headers: { "Content-Type": "audio/mpeg" } });
      }
      return assert.fail(`unexpected ${url}`);
    },
  });
  panel.$("#query").value = "Seed";
  await panel.$("#recommendForm").dispatch("submit");
  await settle();
  assert.equal(panel.$("#seedPlayButton").hidden, false);
  assert.equal(panel.$("#seedPreview").getAttribute("src"), null, "no network until play");
  await panel.$("#seedPlayButton").click();
  await settle();
  const preview = panel.fetches.find(({ url }) => url.includes("/preview/stream?"));
  assert.equal(preview.url, `${ORIGIN}/preview/stream?provider=deezer&provider_track_id=42`);
  assert.equal(preview.init.headers.get("Authorization"), "Bearer token-feature");
  const blobUrl = panel.$("#seedPreview").getAttribute("src");
  assert.match(blobUrl, /^blob:/);
  assert.equal(panel.$("#seedPreview").paused, false);

  panel.push({ ...session("approved"), sessionGeneration: "g-logout" });
  await settle();
  assert.deepEqual(panel.revoked, [blobUrl]);
  assert.equal(panel.$("#seedPreview").getAttribute("src"), null);
});

// These regressions connect authClient + actual panel + background + dist worker.
const { repairHarness, deferred, me, response } = require("./helpers/repairHarness.cjs");

async function connectedPanel(fetchImpl) {
  const h = repairHarness();
  await h.login();
  let panel;
  const originalSend = h.context.chrome.runtime.sendMessage;
  h.context.chrome.runtime.sendMessage = async (message) => {
    if (message.target === "auth-ui") panel?.push(message.state);
    return originalSend(message);
  };
  panel = await loadPanel({ state: h.manager.state(), fetchImpl,
    onMessage: (message) => message.type.startsWith("AUTH_") ? h.send(message) : undefined });
  return { h, panel };
}

test("repair: feature 403 + me429 clears the actual panel Blob, results and EQ", async () => {
  let deny = false;
  const { h, panel } = await connectedPanel((url) => {
    if (url.includes("/preview/stream?")) return new Response(new Uint8Array(8), { headers: { "Content-Type": "audio/mpeg" } });
    if (url.endsWith("/recommend")) return deny
      ? json({ detail: { code: "access_not_approved", access_status: "blocked" } }, 403)
      : json({ track_name: "Seed", artist: "Artist", source_id: "deezer:42", result: { similar: [], reverse: [], hidden: [] } });
    return assert.fail(`unexpected ${url}`);
  });
  panel.$("#query").value = "Seed";
  await panel.$("#recommendForm").dispatch("submit");
  await settle();
  await panel.$("#seedPlayButton").click();
  await settle();
  const blob = panel.$("#seedPreview").getAttribute("src");
  assert.match(blob, /^blob:/);
  const stops = panel.types().filter((type) => type === "STOP_EQ").length;
  h.responses.push(response(429, {}));
  deny = true;
  await panel.$("#recommendForm").dispatch("submit");
  await settle();
  assert.equal(h.manager.state().access.status, "blocked");
  assert.equal(panel.$("#seedSection").hidden, true);
  assert.equal(panel.$("#seedPreview").getAttribute("src"), null);
  assert.ok(panel.revoked.includes(blob));
  assert.ok(panel.types().filter((type) => type === "STOP_EQ").length > stops);
  assert.equal(h.signouts(), 0);
});

test("repair: admin403 + me503 removes admin menu, list and retry state while features stay approved", async () => {
  let denied = false;
  const { h, panel } = await connectedPanel((url) => url.includes("/admin/access-users?")
    ? denied ? json({ detail: { code: "admin_required" } }, 403) : json({ status: "pending", items: [pendingItem], next_cursor: null })
    : assert.fail(`unexpected ${url}`));
  panel.$("#settingsPanel").open = true;
  await panel.$("#settingsPanel").dispatch("toggle");
  await settle();
  assert.equal(panel.$("#adminList").children.length, 1);
  denied = true;
  h.responses.push(response(503, {}));
  await panel.$("#adminReloadButton").click();
  await settle();
  assert.equal(h.manager.state().access.canManage, false);
  assert.equal(panel.$("#adminSettings").hidden, true);
  assert.equal(panel.$("#adminList").children.length, 0);
  assert.equal(h.manager.state().access.status, "approved");
});

test("repair: a delayed A feature403 preserves the actual B panel and sends no B revalidation", async () => {
  const gate = deferred();
  const { h, panel } = await connectedPanel(() => gate.promise);
  panel.$("#query").value = "Seed";
  const pending = panel.$("#recommendForm").dispatch("submit");
  await settle();
  h.responses.push(me("B", "approved", false));
  h.switchTo(h.b);
  await settle();
  const before = h.fetchCalls.length;
  gate.resolve(json({ detail: { code: "access_not_approved", access_status: "blocked" } }, 403));
  await pending;
  await settle();
  assert.equal(h.manager.state().account.uid, "B");
  assert.equal(h.manager.state().access.status, "approved");
  assert.equal(panel.$("#authGate").hidden, true);
  assert.equal(h.fetchCalls.length, before);
});

test("repair boundary: old status replies from the current or retired worker cannot restore the admin menu", async () => {
  const { h, panel } = await connectedPanel(() => json({}));
  const old = h.manager.state();
  panel.push({ ...old, access: { ...old.access, canManage: false }, stateRevision: old.stateRevision + 1 });
  panel.push(old);
  assert.equal(panel.$("#adminSettings").hidden, true);
  panel.push({ ...old, sessionGeneration: "replacement-worker:1", stateRevision: 1,
    access: { ...old.access, canManage: false } });
  panel.push(old);
  assert.equal(panel.$("#adminSettings").hidden, true);
});
