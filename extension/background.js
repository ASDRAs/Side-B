console.log("Background service worker loaded.");
importScripts("scripts/musicTrack.js", "scripts/eqCoordinator.js");

// Use the actual action event so the tab that grants activeTab is also the
// capture target. Opening a global panel is not itself a capture permission.
chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: false })
  .catch((error) => console.error("Failed to set side panel behavior:", error));
chrome.action.onClicked.addListener((tab) => {
  // Call synchronously in the user gesture, before any storage/network await.
  chrome.sidePanel.open({ windowId: tab.windowId })
    .catch((error) => console.error("Failed to open side panel:", error));
  SideBEq.onAction(tab).catch((error) => console.error("Failed to start pending EQ:", error));
});
chrome.tabs.onRemoved.addListener((tabId) => {
  SideBEq.releaseTab(tabId).catch((error) => console.error("Failed to release closed EQ tab:", error));
});
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if ((change.url || change.status) && !SideBEq.isMusicTab(tab)) {
    SideBEq.releaseTab(tabId).catch((error) => console.error("Failed to release navigated EQ tab:", error));
  }
});

const OFFSCREEN_DOCUMENT_PATH = "offscreen.html";
const MUSIC_TAB_URL_PATTERN = "https://music.youtube.com/*";
const YOUTUBE_API_BASE_URL = "https://www.googleapis.com/youtube/v3";
const YOUTUBE_EXPORT_STORAGE_KEY = "youtubeExport";
const MAX_YOUTUBE_RETRIES = 2;
const MAX_YOUTUBE_RETRY_DELAY_MS = 30_000;
const ACTIVE_YOUTUBE_EXPORT_STATES = new Set([
  "awaiting_auth",
  "creating_playlist",
  "adding_items",
]);
const YOUTUBE_QUOTA_REASONS = new Set([
  "quotaExceeded",
  "dailyLimitExceeded",
  "rateLimitExceeded",
  "userRateLimitExceeded",
]);

let creatingOffscreenDocument = null;
let youtubeExportInProgress = false;

// The side panel outlives the tab it was opened from, so it cannot ask
// "which tab is active?" and get a useful answer. The service worker resolves
// the YouTube Music tab for the track reader. EQ separately pins a permitted tab.
async function resolveMusicTab() {
  const tabs = await chrome.tabs.query({ url: MUSIC_TAB_URL_PATTERN });
  if (tabs.length === 0) {
    return null;
  }
  return (
    tabs.find((tab) => tab.audible) || tabs.find((tab) => tab.active) || tabs[0]
  );
}

async function getMusicTab() {
  const tab = await resolveMusicTab();
  return { ok: true, tabId: tab?.id ?? null, url: tab?.url ?? null };
}

async function hasOffscreenDocument() {
  const offscreenUrl = chrome.runtime.getURL(OFFSCREEN_DOCUMENT_PATH);

  const contexts = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
    documentUrls: [offscreenUrl],
  });

  return contexts.length > 0;
}

async function ensureOffscreenDocument() {
  if (await hasOffscreenDocument()) {
    return;
  }

  if (!creatingOffscreenDocument) {
    creatingOffscreenDocument = chrome.offscreen
      .createDocument({
        url: OFFSCREEN_DOCUMENT_PATH,
        reasons: ["USER_MEDIA"],
        justification: "Apply EQ to captured tab audio in the background.",
      })
      .finally(() => {
        creatingOffscreenDocument = null;
      });
  }

  await creatingOffscreenDocument;
}

async function sendToOffscreen(message) {
  await ensureOffscreenDocument();

  const response = await chrome.runtime.sendMessage({
    target: "offscreen",
    ...message,
  });

  if (!response?.ok) {
    throw new Error(response?.error || "Offscreen EQ 처리에 실패했습니다.");
  }

  return response;
}

class YouTubeApiError extends Error {
  constructor(message, status = 0, reason = "") {
    super(message);
    this.name = "YouTubeApiError";
    this.status = status;
    this.reason = reason;
  }
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function ensureYouTubeOAuthConfigured() {
  const clientId = chrome.runtime.getManifest().oauth2?.client_id || "";
  if (!clientId || clientId.startsWith("REPLACE_WITH_")) {
    throw new Error(
      "manifest.json에 Chrome Extension OAuth Client ID를 설정하세요.",
    );
  }
}

async function getYouTubeAuthToken() {
  ensureYouTubeOAuthConfigured();
  const result = await chrome.identity.getAuthToken({
    interactive: true,
    enableGranularPermissions: true,
  });
  const token = typeof result === "string" ? result : result?.token;
  if (!token) {
    throw new Error("Google 계정 인증 토큰을 받지 못했습니다.");
  }
  return token;
}

async function readYouTubeError(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    return new YouTubeApiError(
      `YouTube API 요청이 실패했습니다. (HTTP ${response.status})`,
      response.status,
    );
  }

  const error = payload?.error;
  const reasons = Array.isArray(error?.errors)
    ? error.errors.map((item) => item?.reason).filter(Boolean)
    : [];
  const reason =
    reasons.find((item) => YOUTUBE_QUOTA_REASONS.has(item)) ||
    reasons[0] ||
    error?.status ||
    "";
  const message = error?.message || `YouTube API HTTP ${response.status}`;
  return new YouTubeApiError(message, response.status, reason);
}

function retryDelayMilliseconds(response, retryCount) {
  const retryAfter = response.headers.get("Retry-After");
  if (retryAfter) {
    const seconds = Number(retryAfter);
    if (Number.isFinite(seconds)) {
      return Math.max(0, seconds * 1000);
    }
    const timestamp = Date.parse(retryAfter);
    if (Number.isFinite(timestamp)) {
      return Math.max(0, timestamp - Date.now());
    }
  }
  return 500 * 2 ** retryCount;
}

async function youtubeApiRequest(
  path,
  init,
  auth,
  retryCount = 0,
  authRetried = false,
) {
  if (!auth.token) {
    auth.token = await getYouTubeAuthToken();
  }

  const response = await fetch(`${YOUTUBE_API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init.headers,
      Authorization: `Bearer ${auth.token}`,
    },
  });

  if (response.status === 401 && !authRetried) {
    await chrome.identity.removeCachedAuthToken({ token: auth.token });
    auth.token = null;
    return youtubeApiRequest(path, init, auth, retryCount, true);
  }

  if (
    (response.status === 429 || response.status >= 500) &&
    retryCount < MAX_YOUTUBE_RETRIES
  ) {
    const retryDelay = retryDelayMilliseconds(response, retryCount);
    if (retryDelay > MAX_YOUTUBE_RETRY_DELAY_MS) {
      throw await readYouTubeError(response);
    }
    await sleep(retryDelay);
    return youtubeApiRequest(path, init, auth, retryCount + 1, authRetried);
  }

  if (!response.ok) {
    throw await readYouTubeError(response);
  }
  return response.json();
}

async function storeYouTubeExportState(state) {
  await chrome.storage.local.set({ [YOUTUBE_EXPORT_STORAGE_KEY]: state });
  return state;
}

async function getYouTubeExportState() {
  const stored = await chrome.storage.local.get(YOUTUBE_EXPORT_STORAGE_KEY);
  const state = stored[YOUTUBE_EXPORT_STORAGE_KEY] || null;
  if (
    !state ||
    youtubeExportInProgress ||
    !ACTIVE_YOUTUBE_EXPORT_STATES.has(state.status)
  ) {
    return state;
  }
  return storeYouTubeExportState({
    ...state,
    status: "interrupted",
    error: "이전 YouTube 내보내기 작업이 중단되었습니다. 다시 시도하세요.",
  });
}

function normalizeExportPayload(payload) {
  const title = String(payload?.title || "").trim().slice(0, 150);
  const description = String(payload?.description || "").trim().slice(0, 5000);
  const bucket = String(payload?.bucket || "").trim();
  const requested = Math.max(0, Number(payload?.requested) || 0);
  const matched = Math.max(0, Number(payload?.matched) || 0);
  let deduplicated = Math.max(0, Number(payload?.deduplicated) || 0);
  const skipped = Math.max(0, Number(payload?.skipped) || 0);
  const operationId = String(payload?.operation_id || "").trim();
  const items = Array.isArray(payload?.items) ? payload.items.slice(0, 10) : [];
  const seenVideoIds = new Set();
  const normalizedItems = items
    .map((item) => ({
      videoId: String(item?.video_id || "").trim(),
      name: String(item?.name || "").trim(),
      artist: String(item?.artist || "").trim(),
    }))
    .filter((item) => {
      if (!item.videoId) {
        return false;
      }
      if (seenVideoIds.has(item.videoId)) {
        deduplicated += 1;
        return false;
      }
      seenVideoIds.add(item.videoId);
      return true;
    });

  if (!title) {
    throw new Error("플레이리스트 제목이 비어 있습니다.");
  }
  if (!operationId) {
    throw new Error("YouTube 내보내기 작업 ID가 비어 있습니다.");
  }
  if (normalizedItems.length === 0) {
    throw new Error("플레이리스트에 추가할 곡이 없습니다.");
  }
  return {
    title,
    operationId,
    description,
    bucket,
    requested,
    matched,
    deduplicated,
    skipped,
    items: normalizedItems,
  };
}

async function insertYouTubePlaylist(auth, title, description) {
  return youtubeApiRequest(
    "/playlists?part=id,snippet,status",
    {
      method: "POST",
      body: JSON.stringify({
        snippet: { title, description },
        status: { privacyStatus: "private" },
      }),
    },
    auth,
  );
}

async function insertYouTubePlaylistItem(auth, playlistId, videoId) {
  return youtubeApiRequest(
    "/playlistItems?part=snippet",
    {
      method: "POST",
      body: JSON.stringify({
        snippet: {
          playlistId,
          resourceId: {
            kind: "youtube#video",
            videoId,
          },
        },
      }),
    },
    auth,
  );
}

function publicYouTubeError(error) {
  if (!(error instanceof YouTubeApiError)) {
    return error?.message || "YouTube 플레이리스트 생성에 실패했습니다.";
  }
  if (YOUTUBE_QUOTA_REASONS.has(error.reason) || error.status === 429) {
    return "YouTube API 할당량이 소진되었습니다.";
  }
  if (error.reason === "playlistForbidden") {
    return "이 계정에서는 YouTube 플레이리스트를 만들 수 없습니다.";
  }
  return error.message;
}

function shouldStopAddingItems(error) {
  if (!(error instanceof YouTubeApiError)) {
    return false;
  }
  return (
    error.status === 401 ||
    error.status === 429 ||
    error.status >= 500 ||
    YOUTUBE_QUOTA_REASONS.has(error.reason) ||
    error.reason === "playlistForbidden"
  );
}

async function createYouTubePlaylist(payload) {
  if (youtubeExportInProgress) {
    throw new Error("이미 YouTube 플레이리스트를 생성하고 있습니다.");
  }

  const input = normalizeExportPayload(payload);
  youtubeExportInProgress = true;
  const auth = { token: null };
  let state = {
    status: "awaiting_auth",
    operationId: input.operationId,
    title: input.title,
    bucket: input.bucket,
    requested: input.requested,
    matched: input.matched,
    deduplicated: input.deduplicated,
    toAdd: input.items.length,
    skipped: input.skipped,
    added: 0,
    failed: [],
    playlistId: null,
    youtubeUrl: null,
    youtubeMusicUrl: null,
  };

  try {
    await storeYouTubeExportState(state);
    auth.token = await getYouTubeAuthToken();
    state = await storeYouTubeExportState({
      ...state,
      status: "creating_playlist",
    });

    const playlist = await insertYouTubePlaylist(
      auth,
      input.title,
      input.description,
    );
    const playlistId = String(playlist?.id || "");
    if (!playlistId) {
      throw new Error("YouTube가 플레이리스트 ID를 반환하지 않았습니다.");
    }

    state = await storeYouTubeExportState({
      ...state,
      status: "adding_items",
      playlistId,
      youtubeUrl: `https://www.youtube.com/playlist?list=${playlistId}`,
      youtubeMusicUrl: `https://music.youtube.com/playlist?list=${playlistId}`,
    });

    for (const [index, item] of input.items.entries()) {
      try {
        await insertYouTubePlaylistItem(auth, playlistId, item.videoId);
        state.added += 1;
      } catch (error) {
        const message = publicYouTubeError(error);
        state.failed.push({
          videoId: item.videoId,
          name: item.name,
          artist: item.artist,
          error: message,
        });
        if (shouldStopAddingItems(error)) {
          for (const remaining of input.items.slice(index + 1)) {
            state.failed.push({
              videoId: remaining.videoId,
              name: remaining.name,
              artist: remaining.artist,
              error: message,
            });
          }
          state = await storeYouTubeExportState(state);
          break;
        }
      }
      state = await storeYouTubeExportState(state);
    }

    if (state.added === 0) {
      state = await storeYouTubeExportState({
        ...state,
        status: "error",
        error: "플레이리스트에 추가된 곡이 없습니다.",
      });
      return { ok: false, error: state.error, state };
    }

    state = await storeYouTubeExportState({
      ...state,
      status: state.failed.length > 0 ? "partial" : "completed",
    });
    return { ok: true, state };
  } catch (error) {
    state = await storeYouTubeExportState({
      ...state,
      status: "error",
      error: publicYouTubeError(error),
    });
    throw error;
  } finally {
    auth.token = null;
    youtubeExportInProgress = false;
  }
}

async function handleMessage(message) {
  switch (message.type) {
    case "START_EQ":
      return SideBEq.start(message);

    case "UPDATE_EQ":
      return sendToOffscreen({
        type: "UPDATE_EQ",
        preset: message.preset,
      });

    case "STOP_EQ":
      return SideBEq.stop();

    case "GET_EQ_STATE":
      return SideBEq.getState();

    case "READ_EQ_TRACK":
      return { ok: true, track: await SideBEq.readTrack(message.tabId) };

    case "GET_MUSIC_TAB":
      return getMusicTab();

    case "CREATE_YOUTUBE_PLAYLIST":
      return createYouTubePlaylist(message.payload);

    case "GET_YOUTUBE_EXPORT_STATE":
      return {
        ok: true,
        state: await getYouTubeExportState(),
      };

    default:
      throw new Error(`알 수 없는 background 메시지: ${message.type}`);
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.target !== "background") {
    return false;
  }
  if (message.type !== "READ_EQ_TRACK") console.log("Background received:", message);

  handleMessage(message)
    .then(sendResponse)
    .catch((error) => {
      console.error("Background message error:", error);

      sendResponse({
        ok: false,
        error: error?.message || "알 수 없는 오류가 발생했습니다.",
      });
    });

  return true;
});
