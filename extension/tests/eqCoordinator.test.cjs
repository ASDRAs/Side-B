const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const musicTab = (id, extra = {}) => ({ id, windowId: 1, url: "https://music.youtube.com/watch?v=test", ...extra });
const settle = () => new Promise(setImmediate);

function harness(options = {}) {
  const session = options.session || {};
  const tabs = options.tabs || [musicTab(1, { active: true })];
  const allowed = new Set(options.allowed || []);
  const calls = [];
  let action;
  let removed;
  let updated;
  let now = 1000;
  let offscreen = Boolean(options.audioState?.capturing);
  let audioState = options.audioState || { ok: true, active: false, status: "inactive" };
  const chrome = {
    sidePanel: {
      async setPanelBehavior(value) { calls.push(["behavior", value]); },
      async open(value) { calls.push(["open", value]); },
    },
    action: { onClicked: { addListener(fn) { action = fn; } } },
    runtime: {
      getURL: (file) => `chrome-extension://test/${file}`,
      getContexts: async () => { calls.push(["contexts"]); return offscreen ? [{}] : []; },
      onMessage: { addListener() {} },
      async sendMessage(message) {
        calls.push(["message", message]);
        if (message.target !== "offscreen") return { ok: true };
        if (message.type === "START_EQ") audioState = { ok: true, capturing: true, active: true, tabId: message.tabId, mode: message.mode, status: "applied" };
        if (message.type === "SET_EQ_MODE") audioState.mode = message.mode;
        if (message.type === "STOP_EQ") audioState = { ok: true, active: false, status: "inactive" };
        return { ...audioState };
      },
    },
    offscreen: {
      async createDocument() { offscreen = true; },
      async closeDocument() { offscreen = false; },
    },
    tabs: {
      onRemoved: { addListener(fn) { removed = fn; } },
      onUpdated: { addListener(fn) { updated = fn; } },
      async get(id) { return tabs.find((tab) => tab.id === id); },
      async query(query) {
        calls.push(["query", query]);
        return tabs.filter((tab) => tab.windowId === (query.windowId ?? 1) && tab.url.startsWith("https://music.youtube.com/"));
      },
      async update(id, props) { calls.push(["focus", id]); Object.assign(tabs.find((tab) => tab.id === id), props); },
    },
    windows: { async update(id) { calls.push(["window", id]); } },
    scripting: { async executeScript(details) { calls.push(["script", details]); return [{ result: { title: "Song", artist: "Artist" } }]; } },
    tabCapture: {
      async getMediaStreamId({ targetTabId }) {
        calls.push(["capture", targetTabId]);
        if (!allowed.has(targetTabId)) throw new Error("Extension has not been invoked for the current page (see activeTab permission). Chrome pages cannot be captured.");
        return "stream-id";
      },
    },
    storage: { session: {
      async get(key) { calls.push(["storage:get", key]); return { [key]: session[key] }; },
      async set(values) { Object.assign(session, values); },
      async remove(key) { delete session[key]; },
    } },
  };
  const context = vm.createContext({ chrome, URL, Date: class extends Date { static now() { return now; } }, console: { log() {}, error() {} }, setTimeout, clearTimeout });
  context.importScripts = (...files) => {
    for (const file of files) vm.runInContext(fs.readFileSync(path.join(__dirname, "..", file), "utf8"), context);
  };
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "background.js"), "utf8"), context);
  return { eq: context.SideBEq, calls, session, allowed, action: (tab) => action(tab),
    removed: (id) => removed(id), updated: (id, change, tab) => updated(id, change, tab), advance: (ms) => { now += ms; } };
}

test("native permission rejection becomes an exact-tab toolbar request", async () => {
  const h = harness();
  const state = await h.eq.start({ mode: "auto", windowId: 1 });
  assert.equal(state.status, "awaiting_activation");
  assert.equal(h.session.pendingEqActivation.tabId, 1);
  assert.ok(h.calls.some(([kind, id]) => kind === "focus" && id === 1));
  assert.ok(!h.calls.some(([kind, msg]) => kind === "message" && msg.type === "START_EQ"));
  assert.equal(h.calls[0][1].openPanelOnActionClick, false);
});

test("only a subsequent action on the requested tab resumes EQ", async () => {
  const h = harness({ tabs: [musicTab(1, { active: true }), musicTab(2)] });
  await h.eq.start({ mode: "test" });
  h.allowed.add(2); // Simulates the browser grant, not a programmatic permission grant.
  h.action(musicTab(2));
  await settle();
  assert.equal((await h.eq.getState()).status, "awaiting_activation");
  h.allowed.add(1);
  h.action(musicTab(1));
  await settle();
  assert.equal((await h.eq.getState()).active, true);
  assert.equal((await h.eq.getState()).mode, "test");
  assert.equal(h.session.pendingEqActivation, undefined);
});

test("opening the panel alone never starts audio capture", async () => {
  const h = harness({ allowed: [1] });
  h.action(musicTab(1));
  await settle();
  assert.ok(h.calls.some(([kind]) => kind === "open"));
  assert.ok(!h.calls.some(([kind]) => kind === "capture"));
});

test("pending activation survives a worker restart but expires after two minutes", async () => {
  const first = harness();
  await first.eq.start({ mode: "test" });
  const next = harness({ session: first.session, allowed: [1] });
  assert.equal((await next.eq.getState()).status, "awaiting_activation");
  next.advance(120_001);
  next.action(musicTab(1));
  await settle();
  assert.equal((await next.eq.getState()).status, "inactive");
  assert.ok(!next.calls.some(([kind]) => kind === "capture"));
});

test("STOP cancels a pending activation so later icon clicks cannot start it", async () => {
  const h = harness();
  await h.eq.start({ mode: "auto" });
  await h.eq.stop();
  h.allowed.add(1);
  h.action(musicTab(1));
  await settle();
  assert.equal((await h.eq.getState()).status, "inactive");
  assert.equal(h.calls.filter(([kind]) => kind === "capture").length, 1);
});

test("EQ targets the panel window and active music tab, not an audible tab elsewhere", async () => {
  const h = harness({ allowed: [3], tabs: [musicTab(1, { windowId: 2, audible: true }), musicTab(2, { audible: true }), musicTab(3, { active: true })] });
  const state = await h.eq.start({ mode: "test", windowId: 1 });
  assert.equal(state.tabId, 3);
});

test("a mode change reuses the captured tab when it stops reporting audible", async () => {
  const h = harness({ tabs: [musicTab(1), musicTab(2, { audible: true })], audioState: { ok: true, capturing: true, active: true, tabId: 1, mode: "auto" } });
  const state = await h.eq.start({ mode: "test", windowId: 1 });
  assert.equal(state.tabId, 1);
  assert.equal(state.mode, "test");
  assert.ok(!h.calls.some(([kind]) => kind === "capture"));
});

test("internal pages and other origins are rejected before capture", async () => {
  const h = harness({ tabs: [{ id: 5, url: "chrome://extensions", windowId: 1 }] });
  await assert.rejects(h.eq.start({ tabId: 5, mode: "auto" }), /YouTube Music/);
  assert.ok(!h.calls.some(([kind]) => kind === "capture"));
  await assert.rejects(h.eq.start({ mode: "unknown" }), /EQ/);
});

test("track polling reads the captured tab instead of resolving a new audible tab", async () => {
  const h = harness({ tabs: [musicTab(1), musicTab(2, { audible: true })] });
  assert.equal((await h.eq.readTrack(1)).title, "Song");
  const script = h.calls.find(([kind]) => kind === "script")[1];
  assert.equal(script.target.tabId, 1);
  assert.equal(script.world, "MAIN");
});

test("closing a pending target cancels its activation", async () => {
  const h = harness();
  await h.eq.start({ mode: "auto" });
  h.removed(1);
  await settle();
  assert.equal(h.session.pendingEqActivation, undefined);
  assert.equal((await h.eq.getState()).status, "inactive");
});

test("leaving YouTube Music stops capture, including in test mode", async () => {
  const h = harness({ allowed: [1] });
  await h.eq.start({ mode: "test" });
  h.updated(1, { status: "complete" }, { id: 1, url: "https://example.com/" });
  await settle();
  assert.equal((await h.eq.getState()).status, "inactive");
});

test("same-site song navigation and unrelated tab closure leave EQ running", async () => {
  const h = harness({ allowed: [1] });
  await h.eq.start({ mode: "test" });
  h.updated(1, { url: "https://music.youtube.com/watch?v=another" }, musicTab(1));
  h.removed(2);
  await settle();
  assert.equal((await h.eq.getState()).active, true);
});

for (const mode of ["inactive", "capturing", "pending"]) {
  test(`unrelated tab events do no storage or offscreen work while EQ is ${mode}`, async () => {
    const h = harness({ allowed: mode === "capturing" ? [1] : [] });
    if (mode === "inactive") await h.eq.stop();
    else await h.eq.start({ mode: "test" });
    const before = h.calls.length;
    for (let id = 20; id < 70; id++) {
      // Chrome may omit the URL of tabs outside the extension's host permissions.
      h.updated(id, { status: "loading" }, { id });
      h.updated(id, { status: "complete" }, { id });
      h.removed(id);
    }
    await settle();
    assert.equal(h.calls.length - before, 0);
  });
}

test("a cold worker hydrates EQ owners only once during an unrelated tab event burst", async () => {
  const h = harness({ audioState: { ok: true, capturing: true, active: true, tabId: 1, mode: "test" } });
  for (let id = 20; id < 70; id++) h.updated(id, { status: "complete" }, { id });
  await settle();
  assert.equal(h.calls.filter(([kind]) => kind === "storage:get").length, 1);
  assert.equal(h.calls.filter(([kind, msg]) => kind === "message" && msg.type === "GET_STATE").length, 1);
  assert.equal((await h.eq.getState()).tabId, 1);
});

test("a restarted worker restores both the captured and pending tab owners", async () => {
  const h = harness({
    session: { pendingEqActivation: { tabId: 2, mode: "auto", expiresAt: 121000 } },
    audioState: { ok: true, capturing: true, active: true, tabId: 1, mode: "test" },
  });
  h.updated(99, { status: "complete" }, { id: 99 });
  await settle();
  h.updated(1, { status: "loading" }, { id: 1 });
  await settle();
  assert.ok(h.calls.some(([kind, msg]) => kind === "message" && msg.type === "STOP_EQ"));
  assert.equal((await h.eq.getState()).tabId, 2);
  h.removed(2);
  await settle();
  assert.equal(h.session.pendingEqActivation, undefined);
  assert.equal((await h.eq.getState()).status, "inactive");
});

test("a tab leaving while START is queued is released even when the known owner set was empty", async () => {
  const h = harness({ allowed: [1] });
  await h.eq.stop();
  const starting = h.eq.start({ mode: "test" });
  h.updated(1, { status: "loading" }, { id: 1 });
  await starting;
  await settle();
  assert.ok(h.calls.some(([kind, msg]) => kind === "message" && msg.type === "STOP_EQ"));
  assert.equal((await h.eq.getState()).status, "inactive");
});

test("a denied switch keeps both the captured tab and the pending target releasable", async () => {
  const h = harness({ tabs: [musicTab(1), musicTab(2)], allowed: [1] });
  await h.eq.start({ mode: "test", tabId: 1 });
  await h.eq.start({ mode: "auto", tabId: 2 });
  h.removed(2);
  await settle();
  assert.equal(h.session.pendingEqActivation, undefined);
  assert.equal((await h.eq.getState()).tabId, 1);
  h.updated(1, { status: "complete" }, { id: 1 });
  await settle();
  assert.equal((await h.eq.getState()).status, "inactive");
});
