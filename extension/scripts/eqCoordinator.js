globalThis.SideBEq = (() => {
  const PENDING_KEY = "pendingEqActivation";
  const ACTIVATION_TTL_MS = 120_000;
  let commands = Promise.resolve();
  let queuedCommands = 0;
  let trackedTabIds = null;

  function enqueue(action) {
    queuedCommands += 1;
    const result = commands.then(action).finally(() => { queuedCommands -= 1; });
    commands = result.catch(() => {});
    return result;
  }

  function isMusicTab(tab) {
    try {
      const url = new URL(tab?.url);
      return url.protocol === "https:" && url.hostname === "music.youtube.com";
    } catch {
      return false;
    }
  }

  function publish(state) {
    chrome.runtime.sendMessage({ target: "eq-ui", type: "EQ_STATE_UPDATED", state })
      .catch(() => {});
    return state;
  }

  async function readPending() {
    const pending = (await chrome.storage.session.get(PENDING_KEY))[PENDING_KEY];
    if (!pending) return null;
    if (pending.expiresAt <= Date.now()) {
      await chrome.storage.session.remove(PENDING_KEY);
      return null;
    }
    return pending;
  }

  async function readAudioState() {
    if (!(await hasOffscreenDocument())) {
      return { ok: true, active: false, capturing: false, tabId: null, status: "inactive" };
    }
    return sendToOffscreen({ type: "GET_STATE" });
  }

  // Refresh only inside the command queue; UI polling must not overwrite owners
  // while a new capture target is being acquired. null means a cold worker.
  async function readTargets() {
    const pending = await readPending();
    const state = await readAudioState();
    trackedTabIds = new Set([pending?.tabId, state.tabId].filter(Number.isInteger));
    return { pending, state };
  }

  async function getState() {
    const pending = await readPending();
    if (pending) {
      return { ok: true, active: false, status: "awaiting_activation",
        tabId: pending.tabId, mode: pending.mode };
    }
    return readAudioState();
  }

  async function resolveTarget({ tabId, windowId }, state) {
    if (Number.isInteger(tabId)) {
      const tab = await chrome.tabs.get(tabId);
      if (!isMusicTab(tab)) throw new Error("YouTube Music 탭에서 EQ를 시작하세요.");
      return tab;
    }
    const query = { url: MUSIC_TAB_URL_PATTERN };
    if (Number.isInteger(windowId)) query.windowId = windowId;
    else query.lastFocusedWindow = true;
    const tabs = await chrome.tabs.query(query);
    // A captured tab may no longer be marked audible. Keep that tab unless the
    // user explicitly selects another music tab in the same window.
    const tab = tabs.find((item) => item.active) ||
      tabs.find((item) => item.id === state.tabId) ||
      tabs.find((item) => item.audible) || tabs[0];
    if (!tab || !isMusicTab(tab)) throw new Error("이 창에 YouTube Music 탭을 열어 주세요.");
    return tab;
  }

  async function startNow(options = {}) {
    const mode = options.mode ?? "auto";
    if (!["auto", "test"].includes(mode)) throw new Error("알 수 없는 EQ 모드입니다.");
    const { state } = await readTargets();
    const tab = await resolveTarget(options, state);
    trackedTabIds.add(tab.id);
    if (state.capturing && state.tabId === tab.id) {
      await chrome.storage.session.remove(PENDING_KEY);
      return sendToOffscreen({ type: "SET_EQ_MODE", mode });
    }

    await ensureOffscreenDocument();
    let streamId;
    try {
      streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
    } catch (error) {
      if (!state.capturing) await chrome.offscreen.closeDocument();
      if (!/not been invoked|activeTab permission/i.test(error?.message || "")) throw error;
      // host_permissions and a panel button do not grant tabCapture access.
      // Only a real toolbar action on this exact tab can complete this request.
      // Session storage survives worker suspension; requests expire and STOP cancels them.
      await chrome.storage.session.set({ [PENDING_KEY]: {
        tabId: tab.id, mode, expiresAt: Date.now() + ACTIVATION_TTL_MS,
      } });
      await chrome.tabs.update(tab.id, { active: true });
      if (Number.isInteger(tab.windowId)) await chrome.windows.update(tab.windowId, { focused: true });
      return publish(await getState());
    }

    await chrome.storage.session.remove(PENDING_KEY);
    // The offscreen document stops the previous stream only after permission for
    // the new target succeeds, so a denied switch cannot mute the existing EQ.
    return sendToOffscreen({ type: "START_EQ", streamId, tabId: tab.id, mode });
  }

  function start(options) {
    return enqueue(() => startNow(options));
  }

  function stop() {
    return enqueue(async () => {
      await chrome.storage.session.remove(PENDING_KEY);
      if (await hasOffscreenDocument()) {
        await sendToOffscreen({ type: "STOP_EQ" });
        await chrome.offscreen.closeDocument();
      }
      trackedTabIds = new Set();
      return publish({ ok: true, active: false, capturing: false, tabId: null, status: "inactive" });
    });
  }

  function onAction(tab) {
    return enqueue(async () => {
      const pending = await readPending();
      if (!pending || pending.tabId !== tab.id || !isMusicTab(tab)) return;
      try {
        return await startNow({ tabId: tab.id, mode: pending.mode });
      } catch (error) {
        await chrome.storage.session.remove(PENDING_KEY);
        publish({ ok: false, active: false, status: "error", error: error.message });
      }
    });
  }

  function releaseTab(tabId) {
    // A queued START may not have resolved its tab yet. Preserve event ordering
    // in that case, then recheck after earlier commands (or cold hydration) finish.
    if (queuedCommands === 0 && trackedTabIds && !trackedTabIds.has(tabId)) return Promise.resolve();
    return enqueue(async () => {
      if (trackedTabIds && !trackedTabIds.has(tabId)) return;
      const { pending, state } = await readTargets();
      if (pending?.tabId === tabId) await chrome.storage.session.remove(PENDING_KEY);
      if (state.tabId === tabId) {
        await sendToOffscreen({ type: "STOP_EQ" });
        await chrome.offscreen.closeDocument();
      }
      trackedTabIds.delete(tabId);
      if (pending?.tabId === tabId || state.tabId === tabId) publish(await getState());
    });
  }

  async function readTrack(tabId) {
    const tab = await chrome.tabs.get(tabId);
    if (!isMusicTab(tab)) return null;
    const [result] = await chrome.scripting.executeScript({
      target: { tabId }, world: "MAIN", func: SideBMusic.readTrack,
    });
    return result?.result ?? null;
  }

  return { start, stop, getState, onAction, readTrack, releaseTab, isMusicTab };
})();
