import { readCurrentTrack } from "./scripts/tab.js";
import { startEq, stopEq } from "./scripts/eq.js";
import {
  createYouTubePlaylist,
  getYouTubeExportState,
} from "./scripts/youtubeExport.js";
import {
  exportExclusionCounts,
  failedTrackLabel,
  fetchYouTubeMatches,
  isStateForOperation,
  orderedMatchReviewRows,
  partitionExportableTracks,
  unmatchedReasonLabel,
} from "./scripts/youtubeExportView.js";

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";
const REQUEST_TIMEOUT_MS = 90_000;
const BUCKET_LABELS = {
  similar: "유사한 곡",
  reverse: "저노출 유사곡",
  opposite: "반대 무드",
  hidden: "숨겨진 곡",
};

const form = document.querySelector("#recommendForm");
const apiBaseUrlInput = document.querySelector("#apiBaseUrl");
const youtubeExportTokenInput = document.querySelector("#youtubeExportToken");
const queryInput = document.querySelector("#query");
const submitButton = document.querySelector("#submitButton");
const connectionBadge = document.querySelector("#connectionBadge");
const statusMessage = document.querySelector("#statusMessage");
const seedSection = document.querySelector("#seedSection");
const seedTitle = document.querySelector("#seedTitle");
const seedArtist = document.querySelector("#seedArtist");
const results = document.querySelector("#results");
const rawPanel = document.querySelector("#rawPanel");
const rawResponse = document.querySelector("#rawResponse");
const bucketTemplate = document.querySelector("#bucketTemplate");
const trackTemplate = document.querySelector("#trackTemplate");
const currentTrackButton = document.querySelector("#currentTrackButton");
const currentTrackResult = document.querySelector("#currentTrackResult");
const currentTrackTitle = document.querySelector("#currentTrackTitle");
const currentTrackArtist = document.querySelector("#currentTrackArtist");
const eqTestButton = document.querySelector("#eqTestButton");
const eqStopButton = document.querySelector("#eqStopButton");
const eqTestStatus = document.querySelector("#eqTestStatus");
const youtubeExportPanel = document.querySelector("#youtubeExportPanel");
const youtubeExportStatus = document.querySelector("#youtubeExportStatus");
const youtubeExportTitle = document.querySelector("#youtubeExportTitle");
const youtubeExportDetail = document.querySelector("#youtubeExportDetail");
const youtubeExportFailures = document.querySelector("#youtubeExportFailures");
const youtubeExportLinks = document.querySelector("#youtubeExportLinks");
const youtubeLink = document.querySelector("#youtubeLink");
const youtubeMusicLink = document.querySelector("#youtubeMusicLink");
const youtubeMatchReview = document.querySelector("#youtubeMatchReview");
const youtubeMatchList = document.querySelector("#youtubeMatchList");
const youtubeMatchConfirm = document.querySelector("#youtubeMatchConfirm");
const youtubeMatchCancel = document.querySelector("#youtubeMatchCancel");

let currentRecommendation = null;
let pendingMatchReview = null;
let youtubeExportGeneration = 0;
let activeYouTubeOperationId = null;

// EQ 테스트용
const TEST_EQ_PRESET = {
  preamp: -6,
  bands: [
    { frequency: 31, gain: 12 },
    { frequency: 62, gain: 12 },
    { frequency: 125, gain: 8 },
    { frequency: 250, gain: 4 },
    { frequency: 500, gain: -6 },
    { frequency: 1000, gain: -8 },
    { frequency: 2000, gain: -4 },
    { frequency: 4000, gain: 4 },
    { frequency: 8000, gain: 6 },
    { frequency: 16000, gain: 6 },
  ],
};

function normalizeApiBaseUrl(value) {
  const url = new URL(value.trim());
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("백엔드 주소는 http 또는 https여야 합니다.");
  }
  return url.toString().replace(/\/$/, "");
}

function setState(state, message) {
  const badgeText = {
    idle: "대기",
    loading: "요청 중",
    success: "연결됨",
    error: "실패",
  }[state];

  connectionBadge.className = `badge badge-${state}`;
  connectionBadge.textContent = badgeText;
  statusMessage.className = state === "error" ? "status status-error" : "status";
  statusMessage.textContent = message;
  submitButton.disabled = state === "loading";
}

function clearResults() {
  youtubeExportGeneration += 1;
  if (pendingMatchReview) {
    pendingMatchReview(undefined);
    pendingMatchReview = null;
  }
  currentRecommendation = null;
  activeYouTubeOperationId = null;
  results.replaceChildren();
  seedSection.hidden = true;
  rawPanel.hidden = true;
  rawPanel.open = false;
  rawResponse.textContent = "";
  youtubeExportPanel.hidden = true;
  youtubeExportLinks.hidden = true;
  youtubeLink.removeAttribute("href");
  youtubeMusicLink.removeAttribute("href");
  youtubeExportStatus.textContent = "";
  youtubeExportTitle.textContent = "";
  youtubeExportDetail.textContent = "";
  youtubeExportFailures.hidden = true;
  youtubeExportFailures.replaceChildren();
  youtubeMatchReview.hidden = true;
  youtubeMatchList.replaceChildren();
  setExportButtonsDisabled(false);
}

function renderTrack(track, index) {
  const fragment = trackTemplate.content.cloneNode(true);
  fragment.querySelector(".rank").textContent = String(index + 1);
  fragment.querySelector(".track-title").textContent = track.name || "제목 없음";
  fragment.querySelector(".track-artist").textContent = track.artist || "아티스트 없음";

  const label = fragment.querySelector(".track-label");
  const labelText = track.label || (track.reason_tags || []).join(", ");
  label.textContent = labelText;
  label.hidden = !labelText;
  return fragment;
}

function renderBucket(bucketName, tracks) {
  if (!Array.isArray(tracks) || tracks.length === 0) {
    return;
  }

  const fragment = bucketTemplate.content.cloneNode(true);
  const bucket = fragment.querySelector(".bucket");
  bucket.dataset.bucket = bucketName;
  fragment.querySelector("h2").textContent = BUCKET_LABELS[bucketName] || bucketName;
  fragment.querySelector(".count").textContent = `${tracks.length}곡`;
  const exportButton = fragment.querySelector(".export-button");
  exportButton.addEventListener("click", () => exportBucket(bucketName, tracks));

  const list = fragment.querySelector(".track-list");
  tracks.forEach((track, index) => list.append(renderTrack(track, index)));
  results.append(fragment);
}

function renderResponse(payload) {
  currentRecommendation = payload;
  seedTitle.textContent = payload.track_name || "기준 곡 없음";
  seedArtist.textContent = payload.artist || "";
  seedSection.hidden = false;

  const buckets = payload.result || {};
  Object.keys(BUCKET_LABELS).forEach((name) => renderBucket(name, buckets[name]));

  rawResponse.textContent = JSON.stringify(payload, null, 2);
  rawPanel.hidden = false;
}

function setExportButtonsDisabled(disabled) {
  document.querySelectorAll(".export-button").forEach((button) => {
    button.disabled = disabled;
  });
}

function renderYouTubeExportState(state) {
  if (!state) {
    youtubeExportPanel.hidden = true;
    return;
  }

  const labels = {
    matching: "곡 매칭 중",
    awaiting_auth: "Google 인증 대기",
    creating_playlist: "플레이리스트 생성 중",
    adding_items: "곡 추가 중",
    reviewing: "매칭 확인",
    cancelled: "취소됨",
    interrupted: "중단됨",
    completed: "완료",
    partial: "일부 완료",
    error: "실패",
  };
  const active = [
    "matching",
    "awaiting_auth",
    "creating_playlist",
    "adding_items",
    "reviewing",
  ].includes(state.status);
  const counts = [];
  if (Number.isFinite(state.added) && Number.isFinite(state.toAdd)) {
    counts.push(`${state.added}/${state.toAdd}곡 추가`);
  }
  if (Number.isFinite(state.matched) && Number.isFinite(state.requested)) {
    counts.push(`${state.matched}/${state.requested}곡 매칭`);
  }
  if (state.skipped) {
    counts.push(`${state.skipped}곡 매칭 제외`);
  }
  if (state.deduplicated) {
    counts.push(`${state.deduplicated}곡 중복 제외`);
  }
  if (state.failed?.length) {
    counts.push(`${state.failed.length}곡 추가 실패`);
  }

  youtubeExportPanel.hidden = false;
  if (state.status !== "reviewing") {
    youtubeMatchReview.hidden = true;
  }
  youtubeExportStatus.textContent = labels[state.status] || state.status;
  youtubeExportTitle.textContent = state.title || "Side-B 플레이리스트";
  youtubeExportDetail.textContent = state.error || counts.join(" · ");
  youtubeExportFailures.replaceChildren();
  for (const failure of state.failed || []) {
    const item = document.createElement("li");
    item.textContent = failedTrackLabel(failure);
    youtubeExportFailures.append(item);
  }
  youtubeExportFailures.hidden = !state.failed?.length;
  youtubeExportLinks.hidden = !state.youtubeUrl || !state.youtubeMusicUrl;
  if (state.youtubeUrl && state.youtubeMusicUrl) {
    youtubeLink.href = state.youtubeUrl;
    youtubeMusicLink.href = state.youtubeMusicUrl;
  }
  setExportButtonsDisabled(active);
}

function reviewYouTubeMatches(matches) {
  youtubeMatchList.replaceChildren();
  for (const row of orderedMatchReviewRows(matches)) {
    const { track } = row;
    const item = document.createElement("li");
    item.className = "match-item";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = row.kind === "matched";
    checkbox.disabled = row.kind === "unmatched";
    if (row.kind === "matched") {
      checkbox.dataset.index = String(row.index);
    }
    checkbox.setAttribute(
      "aria-label",
      `${track.artist} - ${track.name}${row.kind === "unmatched" ? " 제외" : ""}`,
    );

    const copy = document.createElement("div");
    copy.className = "match-copy";
    const title = document.createElement("strong");
    title.textContent = `${track.artist} - ${track.name}`;
    if (row.kind === "matched") {
      const match = document.createElement("span");
      match.textContent = `${track.youtube_title} · ${track.channel_title}`;
      const confidence = document.createElement("span");
      confidence.textContent = `확신도 ${Math.round(track.confidence * 100)}%`;
      copy.append(title, match, confidence);
    } else {
      const reason = document.createElement("span");
      reason.textContent = unmatchedReasonLabel(track.reason);
      copy.append(title, reason);
    }
    item.append(checkbox, copy);
    youtubeMatchList.append(item);
  }

  youtubeMatchReview.hidden = false;
  return new Promise((resolve) => {
    pendingMatchReview = resolve;
  });
}

youtubeMatchConfirm.addEventListener("click", () => {
  if (!pendingMatchReview) {
    return;
  }
  const selected = [...youtubeMatchList.querySelectorAll("input:checked")].map(
    (checkbox) => Number(checkbox.dataset.index),
  );
  const resolve = pendingMatchReview;
  pendingMatchReview = null;
  youtubeMatchReview.hidden = true;
  resolve(selected);
});

youtubeMatchCancel.addEventListener("click", () => {
  if (!pendingMatchReview) {
    return;
  }
  const resolve = pendingMatchReview;
  pendingMatchReview = null;
  youtubeMatchReview.hidden = true;
  resolve(null);
});

async function requestYouTubeMatches(
  apiBaseUrl,
  bucketName,
  tracks,
  exportToken,
) {
  return fetchYouTubeMatches(
    fetch,
    apiBaseUrl,
    bucketName,
    tracks,
    exportToken,
    REQUEST_TIMEOUT_MS,
  );
}

async function exportBucket(bucketName, tracks) {
  if (!currentRecommendation) {
    setState("error", "먼저 추천 결과를 요청하세요.");
    return;
  }

  const exportGeneration = ++youtubeExportGeneration;
  const isCurrentExport = () => exportGeneration === youtubeExportGeneration;
  const operationId = crypto.randomUUID();
  activeYouTubeOperationId = operationId;
  setExportButtonsDisabled(true);
  const exportable = partitionExportableTracks(tracks);
  const bucketLabel = BUCKET_LABELS[bucketName] || bucketName;
  const seed = `${currentRecommendation.artist} - ${currentRecommendation.track_name}`;
  const title = `Side-B · ${seed} · ${bucketLabel}`;
  renderYouTubeExportState({
    status: "matching",
    operationId,
    title,
    requested: exportable.requested,
    matched: 0,
    toAdd: 0,
    added: 0,
    skipped: exportable.invalid,
    failed: [],
  });

  try {
    if (exportable.valid.length === 0) {
      throw new Error("곡명과 아티스트가 있는 추천곡이 없습니다.");
    }
    const apiBaseUrl = normalizeApiBaseUrl(apiBaseUrlInput.value);
    const exportToken = youtubeExportTokenInput.value.trim();
    if (!exportToken) {
      throw new Error("YouTube 내보내기 토큰을 입력하세요.");
    }
    await storeYouTubeExportToken(exportToken);
    const matches = await requestYouTubeMatches(
      apiBaseUrl,
      bucketName,
      exportable.valid,
      exportToken,
    );
    if (!isCurrentExport()) {
      return;
    }
    if (!matches.matched?.length) {
      throw new Error("YouTube에서 확실하게 매칭된 곡이 없습니다.");
    }

    renderYouTubeExportState({
      status: "reviewing",
      operationId,
      title,
      requested: exportable.requested,
      matched: matches.matched.length,
      toAdd: matches.matched.length,
      added: 0,
      ...exportExclusionCounts({
        invalid: exportable.invalid,
        unmatched: matches.unmatched?.length || 0,
        deduplicated: matches.deduplicated || 0,
      }),
      failed: [],
    });
    const selectedIndexes = await reviewYouTubeMatches(matches);
    if (!isCurrentExport() || selectedIndexes === undefined) {
      return;
    }
    if (selectedIndexes === null) {
      renderYouTubeExportState({ status: "cancelled", operationId, title });
      return;
    }
    const selectedTracks = selectedIndexes.map((index) => matches.matched[index]);
    if (selectedTracks.length === 0) {
      throw new Error("플레이리스트에 추가할 곡을 하나 이상 선택하세요.");
    }

    const response = await createYouTubePlaylist({
      operation_id: operationId,
      bucket: bucketName,
      title,
      description: `Side-B 추천 결과로 생성됨. 방향: ${bucketLabel}`,
      requested: exportable.requested,
      matched: matches.matched.length,
      ...exportExclusionCounts({
        invalid: exportable.invalid,
        unmatched: matches.unmatched?.length || 0,
        unselected: matches.matched.length - selectedTracks.length,
        deduplicated: matches.deduplicated || 0,
      }),
      items: selectedTracks.map((track) => ({
        video_id: track.video_id,
        name: track.name,
        artist: track.artist,
      })),
    });
    if (isCurrentExport()) {
      renderYouTubeExportState(response.state);
    }
  } catch (error) {
    if (!isCurrentExport()) {
      return;
    }
    const storedState = await getYouTubeExportState().catch(() => null);
    if (!isCurrentExport()) {
      return;
    }
    if (
      storedState?.status === "error" &&
      isStateForOperation(storedState, operationId)
    ) {
      renderYouTubeExportState(storedState);
    } else {
      renderYouTubeExportState({
        status: "error",
        operationId,
        title,
        error:
          error?.name === "AbortError"
            ? "YouTube 곡 매칭 요청 시간이 초과되었습니다."
            : error?.message || "YouTube 내보내기에 실패했습니다.",
      });
    }
  } finally {
    if (isCurrentExport()) {
      setExportButtonsDisabled(false);
    }
  }
}

async function readStoredApiBaseUrl() {
  if (globalThis.chrome?.storage?.local) {
    const stored = await chrome.storage.local.get("apiBaseUrl");
    return stored.apiBaseUrl;
  }
  return localStorage.getItem("apiBaseUrl");
}

async function storeApiBaseUrl(apiBaseUrl) {
  if (globalThis.chrome?.storage?.local) {
    await chrome.storage.local.set({ apiBaseUrl });
    return;
  }
  localStorage.setItem("apiBaseUrl", apiBaseUrl);
}

async function readStoredYouTubeExportToken() {
  if (globalThis.chrome?.storage?.session) {
    const stored = await chrome.storage.session.get("youtubeExportToken");
    return stored.youtubeExportToken;
  }
  return sessionStorage.getItem("youtubeExportToken");
}

async function storeYouTubeExportToken(exportToken) {
  if (globalThis.chrome?.storage?.session) {
    await chrome.storage.session.set({ youtubeExportToken: exportToken });
    return;
  }
  sessionStorage.setItem("youtubeExportToken", exportToken);
}

async function requestRecommendations(apiBaseUrl, query) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(`${apiBaseUrl}/recommend`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, top_n: 10 }),
      signal: controller.signal,
    });

    if (!response.ok) {
      const body = await response.text();
      throw new Error(`HTTP ${response.status}${body ? `: ${body}` : ""}`);
    }

    return await response.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearResults();

  try {
    const apiBaseUrl = normalizeApiBaseUrl(apiBaseUrlInput.value);
    const query = queryInput.value.trim();
    if (!query) {
      throw new Error("검색어를 입력하세요.");
    }

    await storeApiBaseUrl(apiBaseUrl);
    setState("loading", "백엔드에서 추천 결과를 가져오는 중입니다.");
    const payload = await requestRecommendations(apiBaseUrl, query);
    renderResponse(payload);

    const resultCount = Object.values(payload.result || {}).reduce(
      (sum, bucket) => sum + (Array.isArray(bucket) ? bucket.length : 0),
      0,
    );
    setState("success", `추천 결과 ${resultCount}곡을 받았습니다.`);
  } catch (error) {
    const message =
      error?.name === "AbortError"
        ? "요청 시간이 초과되었습니다. 백엔드 로그를 확인하세요."
        : error?.message || "추천 요청에 실패했습니다.";
    setState("error", message);
  }
});

currentTrackButton.addEventListener("click", async () => {
  currentTrackButton.disabled = true;

  try {
    const track = await readCurrentTrack();

    if (!track) {
      currentTrackResult.hidden = true;
      setState("error", "현재 재생 중인 곡을 찾을 수 없습니다.");
      return;
    }

    currentTrackTitle.textContent = track.title;
    currentTrackArtist.textContent = track.artist || "아티스트 정보 없음";
    currentTrackResult.hidden = false;

    setState("success", "현재 재생 중인 곡을 가져왔습니다.");
  } catch (error) {
    console.error("Failed to read current track:", error);

    currentTrackResult.hidden = true;
    setState("error", "현재 곡 정보를 가져오는 데 실패했습니다.");
  } finally {
    currentTrackButton.disabled = false;
  }
});

readStoredApiBaseUrl()
  .then((storedApiBaseUrl) => {
    apiBaseUrlInput.value = storedApiBaseUrl || DEFAULT_API_BASE_URL;
    queryInput.focus();
  })
  .catch(() => {
    apiBaseUrlInput.value = DEFAULT_API_BASE_URL;
  });

readStoredYouTubeExportToken()
  .then((storedToken) => {
    youtubeExportTokenInput.value = storedToken || "";
  })
  .catch(() => {
    youtubeExportTokenInput.value = "";
  });

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local") {
    return;
  }
  const state = changes.youtubeExport?.newValue;
  if (isStateForOperation(state, activeYouTubeOperationId)) {
    renderYouTubeExportState(state);
  }
});

getYouTubeExportState()
  .then((state) => {
    activeYouTubeOperationId = state?.operationId || null;
    renderYouTubeExportState(state);
  })
  .catch(() => {
    youtubeExportPanel.hidden = true;
  });

eqTestButton.addEventListener("click", async () => {
  eqTestButton.disabled = true;

  try {
    eqTestStatus.textContent = "EQ 적용 중...";

    // TEST_EQ_PRESET에 EQ값 넘겨주면 됨
    // 지금은 버튼 누르면 작동하는데, backend에서 해당 값 보낼 수 있도록
    await startEq(TEST_EQ_PRESET);

    eqTestStatus.textContent = "EQ가 적용되었습니다.";
  } catch (error) {
    console.error("Failed to apply EQ:", error);

    eqTestStatus.textContent = `EQ 적용 실패: ${
      error?.message || "알 수 없는 오류"
    }`;
  } finally {
    eqTestButton.disabled = false;
  }
});

eqStopButton.addEventListener("click", async () => {
  eqStopButton.disabled = true;

  try {
    await stopEq();

    eqTestStatus.textContent = "EQ를 해제했습니다.";
  } catch (error) {
    console.error("Failed to stop EQ:", error);

    eqTestStatus.textContent = `EQ 해제 실패: ${
      error?.message || "알 수 없는 오류"
    }`;
  } finally {
    eqStopButton.disabled = false;
  }
});
