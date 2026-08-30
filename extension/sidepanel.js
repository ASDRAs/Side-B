import { NoMusicTabError, readCurrentTrack } from "./scripts/tab.js";
import { getEqState, startEq, stopEq } from "./scripts/eq.js";
import {
  API_BASE_URL_STORAGE_VERSION,
  DEFAULT_API_BASE_URL,
  backendErrorMessage,
  previewQueryParams,
  recommendationHeaders,
  requiresBackendAccessToken,
  resolveApiBaseUrlSetting,
} from "./scripts/apiConfig.js";
import {
  createYouTubePlaylist,
  getYouTubeExportState,
} from "./scripts/youtubeExport.js";
import {
  apiErrorMessage,
  exportExclusionCounts,
  failedTrackLabel,
  fetchYouTubeMatches,
  isStateForOperation,
  orderedMatchReviewRows,
  partitionExportableTracks,
  shouldAutoSelectMatch,
  unmatchedReasonLabel,
} from "./scripts/youtubeExportView.js";

const REQUEST_TIMEOUT_MS = 90_000;
const ACCESS_TOKEN_KEY = "backendAccessToken";
const LAST_QUERY_KEY = "lastQuery";
const RECENT_QUERIES_KEY = "recentQueries";
const MAX_RECENT_QUERIES = 5;
const BUCKET_LABELS = {
  similar: "유사한 곡",
  reverse: "저노출 유사곡",
  opposite: "반대 무드",
  hidden: "숨겨진 곡",
};
const EXPORT_STATUS_LABELS = {
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
const ACTIVE_EXPORT_STATUSES = new Set([
  "matching",
  "awaiting_auth",
  "creating_playlist",
  "adding_items",
  "reviewing",
]);
const EXPORT_TONES = {
  completed: "success",
  partial: "success",
  error: "error",
  interrupted: "error",
};

const form = document.querySelector("#recommendForm");
const apiBaseUrlInput = document.querySelector("#apiBaseUrl");
const backendAccessTokenInput = document.querySelector("#backendAccessToken");
const tokenRevealButton = document.querySelector("#tokenRevealButton");
const tokenClearButton = document.querySelector("#tokenClearButton");
const tokenStatus = document.querySelector("#tokenStatus");
const historyClearButton = document.querySelector("#historyClearButton");
const historyStatus = document.querySelector("#historyStatus");
const queryHistory = document.querySelector("#queryHistory");
const settingsPanel = document.querySelector("#settingsPanel");
const queryInput = document.querySelector("#query");
const submitButton = document.querySelector("#submitButton");
const connectionBadge = document.querySelector("#connectionBadge");
const statusMessage = document.querySelector("#statusMessage");
const seedSection = document.querySelector("#seedSection");
const seedTitle = document.querySelector("#seedTitle");
const seedArtist = document.querySelector("#seedArtist");
const seedArt = document.querySelector("#seedArt");
const seedPreview = document.querySelector("#seedPreview");
const seedPreviewNote = document.querySelector("#seedPreviewNote");
const emptyState = document.querySelector("#emptyState");
const loadingSkeleton = document.querySelector("#loadingSkeleton");
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
const youtubeMatchSelectAll = document.querySelector("#youtubeMatchSelectAll");
const matchReviewSubtitle = document.querySelector("#matchReviewSubtitle");

let currentRecommendation = null;
let pendingMatchReview = null;
let youtubeExportGeneration = 0;
let activeYouTubeOperationId = null;
let recentQueries = [];

// EQ 테스트용. 백엔드가 곡별 프리셋을 보내기 전까지 쓰는 고정값이다.
// preamp는 필터단 최대 이득(+16.18 dB @ 61 Hz)을 상쇄하도록 잡는다. -6이면
// 최종 +10.2 dB라 0 dBFS 근처 음원에서 클리핑한다.
const TEST_EQ_PRESET = {
  preamp: -17,
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

function setStatus(message, isError = false) {
  statusMessage.className = isError ? "status status-error" : "status";
  statusMessage.textContent = message;
}

// 배지는 백엔드 연결 상태만 나타낸다. EQ와 현재 곡은 각자 자리에서 알린다.
function setState(state, message) {
  const badgeText = {
    idle: "대기",
    loading: "요청 중",
    success: "연결됨",
    error: "실패",
  }[state];

  connectionBadge.className = `badge badge-${state}`;
  connectionBadge.textContent = badgeText;
  setStatus(message, state === "error");
  submitButton.disabled = state === "loading";
}

function renderTokenStatus(token) {
  const value = String(token || "").trim();
  // 마지막 4자리만 보여 어떤 토큰이 들어 있는지 식별할 수 있게 한다.
  tokenStatus.textContent = value
    ? `저장됨 · ${"•".repeat(4)}${value.slice(-4)}`
    : "저장된 토큰 없음";
  tokenClearButton.hidden = !value;
}

function renderQueryHistory(queries) {
  recentQueries = Array.isArray(queries) ? queries : [];
  queryHistory.replaceChildren(
    ...recentQueries.map((value) => {
      const option = document.createElement("option");
      option.value = value;
      return option;
    }),
  );
  historyStatus.textContent = recentQueries.length
    ? `검색 기록 ${recentQueries.length}개`
    : "검색 기록 없음";
  historyClearButton.hidden = recentQueries.length === 0;
}

async function rememberQuery(query) {
  const next = [query, ...recentQueries.filter((item) => item !== query)].slice(
    0,
    MAX_RECENT_QUERIES,
  );
  renderQueryHistory(next);
  await writeLocal({ [LAST_QUERY_KEY]: query, [RECENT_QUERIES_KEY]: next });
}

function openSettings() {
  settingsPanel.open = true;
  settingsPanel.scrollIntoView({ block: "nearest" });
}

function showView(view) {
  emptyState.hidden = view !== "empty";
  loadingSkeleton.hidden = view !== "loading";
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
  resetSeedMedia();
  seedSection.hidden = true;
  rawPanel.hidden = true;
  rawPanel.open = false;
  rawResponse.textContent = "";
  youtubeExportPanel.hidden = true;
  youtubeExportPanel.removeAttribute("data-tone");
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
  fragment.querySelector(".track-artist").textContent =
    track.artist || "아티스트 없음";

  const label = fragment.querySelector(".track-label");
  const labelText = track.label || (track.reason_tags || []).join(", ");
  label.textContent = labelText;
  label.hidden = !labelText;
  return fragment;
}

function renderBucket(bucketName, tracks) {
  if (!Array.isArray(tracks) || tracks.length === 0) {
    return 0;
  }

  const fragment = bucketTemplate.content.cloneNode(true);
  const bucket = fragment.querySelector(".bucket");
  bucket.dataset.bucket = bucketName;
  fragment.querySelector("h2").textContent =
    BUCKET_LABELS[bucketName] || bucketName;
  fragment.querySelector(".count").textContent = `${tracks.length}곡`;
  const exportButton = fragment.querySelector(".export-button");
  exportButton.addEventListener("click", () => exportBucket(bucketName, tracks));

  const list = fragment.querySelector(".track-list");
  tracks.forEach((track, index) => list.append(renderTrack(track, index)));
  results.append(fragment);
  return tracks.length;
}

function renderSeedArtwork(payload) {
  const artworkUrl = String(payload.album_art_url || "").trim();
  seedArt.hidden = !artworkUrl;
  if (artworkUrl) {
    seedArt.src = artworkUrl;
  } else {
    seedArt.removeAttribute("src");
  }
}

function renderSeedPreview(payload, apiBaseUrl) {
  const params = previewQueryParams(payload);
  seedPreviewNote.hidden = true;
  seedPreviewNote.textContent = "";

  if (!params) {
    seedPreview.hidden = true;
    return;
  }

  // preload="none"이라 재생을 누를 때만 공급자 API를 호출한다.
  seedPreview.src = `${apiBaseUrl}/preview/stream?${params}`;
  seedPreview.hidden = false;
}

function resetSeedMedia() {
  seedPreview.pause();
  seedPreview.removeAttribute("src");
  seedPreview.load();
  seedPreview.hidden = true;
  seedPreviewNote.hidden = true;
  seedArt.removeAttribute("src");
  seedArt.hidden = true;
}

function renderResponse(payload, apiBaseUrl) {
  currentRecommendation = payload;
  seedTitle.textContent = payload.track_name || "기준 곡 없음";
  seedArtist.textContent = payload.artist || "";
  renderSeedArtwork(payload);
  renderSeedPreview(payload, apiBaseUrl);
  seedSection.hidden = false;

  const buckets = payload.result || {};
  const rendered = Object.keys(BUCKET_LABELS).reduce(
    (sum, name) => sum + renderBucket(name, buckets[name]),
    0,
  );

  rawResponse.textContent = JSON.stringify(payload, null, 2);
  rawPanel.hidden = false;
  return rendered;
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

  const active = ACTIVE_EXPORT_STATUSES.has(state.status);
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
  youtubeExportPanel.dataset.tone =
    EXPORT_TONES[state.status] || (active ? "active" : "neutral");
  if (state.status !== "reviewing") {
    youtubeMatchReview.hidden = true;
  }
  youtubeExportStatus.textContent =
    EXPORT_STATUS_LABELS[state.status] || state.status;
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

function confidenceTier(confidence) {
  if (!Number.isFinite(confidence)) {
    return { className: "confidence-none", text: "확신도 없음" };
  }
  const percent = Math.round(confidence * 100);
  if (confidence >= 0.85) {
    return { className: "confidence-high", text: `확신도 높음 ${percent}%` };
  }
  if (confidence >= 0.6) {
    return { className: "confidence-medium", text: `확신도 보통 ${percent}%` };
  }
  return { className: "confidence-low", text: `확신도 낮음 ${percent}%` };
}

function matchSelectionCheckboxes() {
  return [...youtubeMatchList.querySelectorAll("input[type=checkbox]")].filter(
    (checkbox) => !checkbox.disabled,
  );
}

function updateMatchSelectionSummary() {
  const checkboxes = matchSelectionCheckboxes();
  const selected = checkboxes.filter((checkbox) => checkbox.checked).length;
  const excluded = youtubeMatchList.querySelectorAll(
    '.match-item[data-kind="unmatched"]',
  ).length;

  const parts = [`${selected}/${checkboxes.length}곡 선택됨`];
  if (excluded) {
    parts.push(`${excluded}곡은 매칭 실패로 제외`);
  }
  matchReviewSubtitle.textContent = parts.join(" · ");

  youtubeMatchSelectAll.checked =
    checkboxes.length > 0 && selected === checkboxes.length;
  youtubeMatchSelectAll.indeterminate =
    selected > 0 && selected < checkboxes.length;
  youtubeMatchSelectAll.disabled = checkboxes.length === 0;
  youtubeMatchConfirm.disabled = selected === 0;
}

function reviewYouTubeMatches(matches) {
  youtubeMatchList.replaceChildren();
  for (const row of orderedMatchReviewRows(matches)) {
    const { track } = row;
    const item = document.createElement("li");
    item.className = "match-item";
    item.dataset.kind = row.kind;

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = row.kind === "matched" && shouldAutoSelectMatch(track);
    checkbox.disabled = row.kind === "unmatched";
    if (row.kind === "matched") {
      checkbox.dataset.index = String(row.index);
    }
    checkbox.setAttribute(
      "aria-label",
      `${track.artist} - ${track.name}${row.kind === "unmatched" ? " 제외" : ""}`,
    );
    checkbox.addEventListener("change", updateMatchSelectionSummary);

    // 왼쪽은 추천된 원본, 오른쪽은 YouTube가 고른 영상. 같은 줄에서 대조한다.
    const origin = document.createElement("div");
    origin.className = "match-origin";
    const originName = document.createElement("strong");
    originName.textContent = track.name;
    const originArtist = document.createElement("span");
    originArtist.className = "match-sub";
    originArtist.textContent = track.artist;
    origin.append(originName, originArtist);

    const target = document.createElement("div");
    target.className = "match-target";
    const meta = document.createElement("div");
    meta.className = "match-meta";

    if (row.kind === "matched") {
      const targetTitle = document.createElement("strong");
      targetTitle.textContent = track.youtube_title;
      const targetChannel = document.createElement("span");
      targetChannel.className = "match-sub";
      targetChannel.textContent = track.channel_title;

      const tier = confidenceTier(track.confidence);
      const confidence = document.createElement("span");
      confidence.className = `confidence ${tier.className}`;
      confidence.textContent = tier.text;
      meta.append(confidence);

      if (track.auto_selected === false) {
        item.dataset.review = "needed";
        const note = document.createElement("span");
        note.className = "match-note";
        note.textContent = "직접 확인 필요";
        meta.append(note);
      }

      target.append(targetTitle, targetChannel, meta);
    } else {
      const reason = document.createElement("span");
      reason.className = "confidence confidence-none";
      reason.textContent = unmatchedReasonLabel(track.reason);
      meta.append(reason);
      target.append(meta);
    }

    item.append(checkbox, origin, target);
    youtubeMatchList.append(item);
  }

  updateMatchSelectionSummary();
  youtubeMatchReview.hidden = false;
  youtubeMatchConfirm.focus();
  return new Promise((resolve) => {
    pendingMatchReview = resolve;
  });
}

function resolveMatchReview(value) {
  if (!pendingMatchReview) {
    return;
  }
  const resolve = pendingMatchReview;
  pendingMatchReview = null;
  youtubeMatchReview.hidden = true;
  resolve(value);
}

// 앨범 아트가 깨지면 자리만 차지하므로 숨긴다.
seedArt.addEventListener("error", () => {
  seedArt.hidden = true;
});

// 공급자 CDN 만료나 404는 재생을 눌러야 드러난다. 조용히 실패하지 않게 한다.
seedPreview.addEventListener("error", () => {
  if (!seedPreview.getAttribute("src")) {
    return;
  }
  seedPreviewNote.textContent = "미리 듣기를 불러오지 못했습니다.";
  seedPreviewNote.hidden = false;
});

seedPreview.addEventListener("playing", () => {
  seedPreviewNote.hidden = true;
});

youtubeMatchSelectAll.addEventListener("change", () => {
  const shouldCheck = youtubeMatchSelectAll.checked;
  matchSelectionCheckboxes().forEach((checkbox) => {
    checkbox.checked = shouldCheck;
  });
  updateMatchSelectionSummary();
});

youtubeMatchConfirm.addEventListener("click", () => {
  const selected = matchSelectionCheckboxes()
    .filter((checkbox) => checkbox.checked)
    .map((checkbox) => Number(checkbox.dataset.index));
  resolveMatchReview(selected);
});

youtubeMatchCancel.addEventListener("click", () => resolveMatchReview(null));

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !youtubeMatchReview.hidden) {
    resolveMatchReview(null);
  }
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
    const exportToken = backendAccessTokenInput.value.trim();
    if (!exportToken) {
      openSettings();
      throw new Error("설정에서 팀 백엔드 토큰을 입력하세요.");
    }
    await storeBackendAccessToken(exportToken);
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
      throw new Error("YouTube에서 확인할 수 있는 곡 후보가 없습니다.");
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

// chrome.storage.local은 암호화되지 않는다. 확장 프로그램에는 OS 키체인에
// 접근하는 API가 없어 더 나은 저장소가 없다. 팀 공용 개발 토큰이고 매번
// 재입력을 강제하면 메모장에 붙여넣는 더 나쁜 길로 가므로 이 거래를 택한다.
// 대신 설정에 삭제 버튼을 둔다.
async function readLocal(keys) {
  if (globalThis.chrome?.storage?.local) {
    return chrome.storage.local.get(keys);
  }
  const stored = {};
  for (const key of keys) {
    const raw = localStorage.getItem(key);
    if (raw !== null) {
      stored[key] = key === RECENT_QUERIES_KEY ? JSON.parse(raw) : raw;
    }
  }
  return stored;
}

async function writeLocal(values) {
  if (globalThis.chrome?.storage?.local) {
    await chrome.storage.local.set(values);
    return;
  }
  for (const [key, value] of Object.entries(values)) {
    localStorage.setItem(
      key,
      typeof value === "string" ? value : JSON.stringify(value),
    );
  }
}

async function removeLocal(keys) {
  if (globalThis.chrome?.storage?.local) {
    await chrome.storage.local.remove(keys);
    return;
  }
  keys.forEach((key) => localStorage.removeItem(key));
}

async function storeApiBaseUrl(apiBaseUrl) {
  await writeLocal({
    apiBaseUrl,
    apiBaseUrlStorageVersion: API_BASE_URL_STORAGE_VERSION,
  });
}

async function storeBackendAccessToken(accessToken) {
  const token = String(accessToken || "").trim();
  if (!token) {
    await removeLocal([ACCESS_TOKEN_KEY]);
  } else {
    await writeLocal({ [ACCESS_TOKEN_KEY]: token });
  }
  renderTokenStatus(token);
}

async function requestRecommendations(apiBaseUrl, query, accessToken) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(`${apiBaseUrl}/recommend`, {
      method: "POST",
      headers: recommendationHeaders(accessToken),
      body: JSON.stringify({ query, top_n: 10 }),
      signal: controller.signal,
    });

    if (!response.ok) {
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        // JSON이 아니면 status만으로 안내한다.
      }
      throw new Error(
        backendErrorMessage(
          response.status,
          apiErrorMessage(payload, ""),
          response.headers.get("Retry-After"),
        ),
      );
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
    const accessToken = backendAccessTokenInput.value.trim();
    const query = queryInput.value.trim();
    if (!query) {
      throw new Error("검색어를 입력하세요.");
    }

    if (requiresBackendAccessToken(apiBaseUrl) && !accessToken) {
      openSettings();
      throw new Error("배포 백엔드 사용에는 팀 백엔드 토큰이 필요합니다.");
    }

    await Promise.all([
      storeApiBaseUrl(apiBaseUrl),
      accessToken ? storeBackendAccessToken(accessToken) : Promise.resolve(),
    ]);
    showView("loading");
    setState("loading", "백엔드에서 추천 결과를 가져오는 중입니다. 최대 90초까지 걸릴 수 있습니다.");
    const payload = await requestRecommendations(apiBaseUrl, query, accessToken);
    const resultCount = renderResponse(payload, apiBaseUrl);
    showView(resultCount === 0 ? "empty" : "none");
    await rememberQuery(query);

    if (resultCount === 0) {
      setState("success", "추천 결과가 없습니다. 다른 검색어를 시도해 보세요.");
      return;
    }
    setState("success", `추천 결과 ${resultCount}곡을 받았습니다.`);
  } catch (error) {
    showView("empty");
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
      setStatus("YouTube Music에서 재생 중인 곡을 찾을 수 없습니다.", true);
      return;
    }

    currentTrackTitle.textContent = track.title;
    currentTrackArtist.textContent = track.artist || "아티스트 정보 없음";
    currentTrackResult.hidden = false;

    // 가져온 곡을 검색어에 그대로 넣어 바로 요청할 수 있게 한다.
    queryInput.value = track.artist
      ? `${track.artist} - ${track.title}`
      : track.title;
    queryInput.focus();
    setStatus("현재 재생 중인 곡을 검색어에 넣었습니다.");
  } catch (error) {
    console.error("Failed to read current track:", error);

    currentTrackResult.hidden = true;
    setStatus(
      error instanceof NoMusicTabError
        ? "YouTube Music 탭을 열어 두면 재생 중인 곡을 가져올 수 있습니다."
        : "현재 곡 정보를 가져오는 데 실패했습니다.",
      true,
    );
  } finally {
    currentTrackButton.disabled = false;
  }
});

readLocal([
  "apiBaseUrl",
  "apiBaseUrlStorageVersion",
  ACCESS_TOKEN_KEY,
  LAST_QUERY_KEY,
  RECENT_QUERIES_KEY,
])
  .then(async (stored) => {
    const resolved = resolveApiBaseUrlSetting(
      stored.apiBaseUrl,
      stored.apiBaseUrlStorageVersion,
    );
    // 저장소 읽기는 비동기라, 그 사이 사용자가 입력했다면 덮어쓰지 않는다.
    if (!apiBaseUrlInput.value) {
      apiBaseUrlInput.value = resolved.apiBaseUrl;
    }
    if (!backendAccessTokenInput.value) {
      backendAccessTokenInput.value = stored[ACCESS_TOKEN_KEY] || "";
    }
    renderTokenStatus(backendAccessTokenInput.value);
    renderQueryHistory(stored[RECENT_QUERIES_KEY]);
    if (!queryInput.value) {
      queryInput.value = stored[LAST_QUERY_KEY] || "";
    }
    // 토큰이 없으면 설정을 펼쳐 첫 사용자가 헤매지 않게 한다.
    settingsPanel.open = !backendAccessTokenInput.value;

    if (resolved.shouldPersist) {
      await storeApiBaseUrl(resolved.apiBaseUrl);
    }
    queryInput.focus();
    // 복원한 검색어는 전체 선택해 바로 덮어쓸 수 있게 한다.
    queryInput.select();
  })
  .catch(() => {
    apiBaseUrlInput.value = DEFAULT_API_BASE_URL;
    settingsPanel.open = !backendAccessTokenInput.value;
  });

// 지연 없이 곧바로 저장한다. change는 blur에서만 발생하고, 디바운스 타이머는
// 붙여넣고 바로 패널을 닫으면 실행되기 전에 문서가 사라진다. storage.set은
// 호출 즉시 브라우저 프로세스로 넘어가므로 문서가 죽어도 기록이 남는다.
backendAccessTokenInput.addEventListener("input", () => {
  storeBackendAccessToken(backendAccessTokenInput.value).catch((error) => {
    console.error("Failed to store the access token:", error);
  });
});

tokenRevealButton.addEventListener("click", () => {
  const reveal = backendAccessTokenInput.type === "password";
  backendAccessTokenInput.type = reveal ? "text" : "password";
  tokenRevealButton.textContent = reveal ? "숨기기" : "보기";
  tokenRevealButton.setAttribute("aria-pressed", String(reveal));
});

tokenClearButton.addEventListener("click", async () => {
  backendAccessTokenInput.value = "";
  await storeBackendAccessToken("");
  backendAccessTokenInput.focus();
});

historyClearButton.addEventListener("click", async () => {
  renderQueryHistory([]);
  await removeLocal([LAST_QUERY_KEY, RECENT_QUERIES_KEY]);
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local") {
    return;
  }
  const state = changes.youtubeExport?.newValue;
  if (isStateForOperation(state, activeYouTubeOperationId)) {
    renderYouTubeExportState(state);
  }

  // 창마다 사이드 패널이 따로 뜬다. 다른 패널에서 지운 토큰이 이쪽에 남아 있으면
  // 다음 추천 요청이 그 값을 그대로 다시 저장해 삭제가 되돌아간다.
  if (changes[ACCESS_TOKEN_KEY]) {
    const nextToken = changes[ACCESS_TOKEN_KEY].newValue || "";
    // 포커스 여부와 무관하게 반영한다. 입력 중이라고 건너뛰면 그 패널의 다음
    // 추천 요청이 낡은 값을 다시 저장해 삭제가 되돌아간다. 값이 같을 때는
    // 건드리지 않아 커서 위치를 지킨다(자기 자신이 만든 변경도 여기로 온다).
    if (backendAccessTokenInput.value !== nextToken) {
      backendAccessTokenInput.value = nextToken;
    }
    renderTokenStatus(nextToken);
  }
  if (changes[RECENT_QUERIES_KEY]) {
    renderQueryHistory(changes[RECENT_QUERIES_KEY].newValue);
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

// offscreen EQ는 패널을 닫아도 계속 돌아간다. 열 때 실제 상태를 읽지 않으면
// 표시와 소리가 어긋난다.
getEqState()
  .then((state) => {
    eqTestStatus.textContent = state?.active
      ? "EQ가 적용되어 있습니다."
      : "EQ가 적용되지 않았습니다.";
  })
  .catch(() => {
    eqTestStatus.textContent = "EQ 상태를 확인하지 못했습니다.";
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
