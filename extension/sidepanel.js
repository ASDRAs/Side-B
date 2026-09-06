import { NoMusicTabError, readCurrentTrack } from "./scripts/tab.js";
import {
  BUCKET_LABELS,
  defaultBucketIndex,
  executedBuckets,
  totalTrackCount,
} from "./scripts/buckets.js";
import {
  youtubeMusicSearchLabel,
  youtubeMusicSearchUrl,
} from "./scripts/youtubeMusicUrl.js";
import { requestErrorMessage, timeoutReason } from "./scripts/requestState.js";
import { getEqState, startEq, stopEq } from "./scripts/eq.js";
import { eqStatusText } from "./scripts/eqView.js";
import {
  API_BASE_URL_STORAGE_VERSION,
  DEFAULT_API_BASE_URL,
  backendErrorMessage,
  previewQueryParams,
  apiErrorMessage,
  recommendationHeaders,
  requiresBackendAccessToken,
  resolveApiBaseUrlSetting,
} from "./scripts/apiConfig.js";
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
  shouldAutoSelectMatch,
  unmatchedReasonLabel,
} from "./scripts/youtubeExportView.js";

const REQUEST_TIMEOUT_MS = 90_000;
const ACCESS_TOKEN_KEY = "backendAccessToken";
const LAST_QUERY_KEY = "lastQuery";
const RECENT_QUERIES_KEY = "recentQueries";
const MAX_RECENT_QUERIES = 5;
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
const settingsToggle = document.querySelector("#settingsToggle");
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
const bucketTabs = document.querySelector("#bucketTabs");
const bucketTemplate = document.querySelector("#bucketTemplate");
const trackTemplate = document.querySelector("#trackTemplate");
const currentTrackButton = document.querySelector("#currentTrackButton");
const eqTestButton = document.querySelector("#eqTestButton");
const eqStopButton = document.querySelector("#eqStopButton");
const eqTestStatus = document.querySelector("#eqTestStatus");
const eqTrack = document.querySelector("#eqTrack");
const eqModes = document.querySelectorAll('input[name="eqMode"]');
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
let renderedBuckets = [];
let selectedBucketIndex = 0;
// 활성 요청 하나만 추적한다. 취소와 타임아웃은 같은 controller를 서로 다른
// reason으로 중단해 구분한다.
let activeRequest = null;
// Track user actions before the asynchronous current-track lookup starts.
let recommendationIntent = 0;
// 추천 요청이 진행 중인 동안에는 화면에 남은 이전 결과를 내보낼 수 없다.
// 내보내기 진행 여부와는 별개의 사유라 따로 둔다.
let requestPending = false;
let exportInFlight = false;
let matchReviewOpener = null;
// 첫 실행에서 우리가 연 설정만 자동으로 닫는다. 사용자가 직접 연 설정을
// 닫아버리면 편집 중이던 값을 가린다.
let settingsAutoOpened = false;
// 저장소 읽기가 끝나기 전에 사용자가 설정을 직접 여닫았는지. 읽기가 늦게
// 도착해 그 조작을 되돌리면 커서 아래에서 패널이 닫힌다.
let settingsUserToggled = false;

// 요청을 보내기 전에 발견한 문제. 서버에 닿아 본 적이 없으므로 연결 배지를
// 실패로 바꾸면 안 된다. 사용자가 고칠 곳은 입력란이지 서버가 아니다.
class PreflightError extends Error {
  constructor(message) {
    super(message);
    this.name = "PreflightError";
  }
}

function normalizeApiBaseUrl(value) {
  let url;
  try {
    url = new URL(value.trim());
  } catch {
    // URL 생성자의 TypeError를 그대로 올리면 "Invalid URL"이 사용자에게 그대로
    // 보이고, 요청을 보낸 적도 없는데 연결 실패로 분류된다.
    throw new PreflightError("백엔드 주소 형식이 올바르지 않습니다.");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new PreflightError("백엔드 주소는 http 또는 https여야 합니다.");
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
    idle: "서버 대기",
    loading: "요청 중",
    success: "연결됨",
    error: "연결 실패",
  }[state];

  connectionBadge.className = `badge badge-${state}`;
  connectionBadge.textContent = badgeText;
  setStatus(message, state === "error");
  // 요청 중에도 제출 버튼은 눌러야 한다. 그때는 취소 명령이기 때문이다.
  // 비활성화는 setRequestPending이 currentTrackButton에만 적용한다.
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

settingsToggle.addEventListener("click", () => {
  settingsUserToggled = true;
  if (settingsPanel.open) {
    settingsPanel.open = false;
    return;
  }
  openSettings();
});

// 헤더 명령과 패널의 summary가 같은 disclosure를 조작한다. 어느 쪽을 눌렀든
// 사용자의 조작이므로 둘 다 기록한다. toggle 이벤트는 비동기로 발생해
// 프로그램 변경과 사용자 조작을 구분할 수 없어 여기서 잡는다.
settingsPanel.querySelector("summary")?.addEventListener("click", () => {
  settingsUserToggled = true;
});

// summary를 직접 눌러 여닫아도 헤더 명령의 상태가 따라간다.
settingsPanel.addEventListener("toggle", () => {
  settingsToggle.setAttribute("aria-expanded", String(settingsPanel.open));
});

// 자동으로 연 설정만 나중에 자동으로 닫는다.
function openSettingsForOnboarding() {
  // 저장소 읽기는 비동기다. 그 사이 사용자가 설정을 직접 여닫았다면 이 결정은
  // 이미 늦었다. 커서 아래에서 패널을 닫아버리지 않는다.
  if (settingsUserToggled) {
    return;
  }
  settingsAutoOpened = !backendAccessTokenInput.value;
  settingsPanel.open = settingsAutoOpened;
}

function showView(view) {
  emptyState.hidden = view !== "empty";
  loadingSkeleton.hidden = view !== "loading";
}

// 요청을 시작할 때는 진행 중인 내보내기만 무효화하고 추천 DOM은 남긴다.
// 여기서 결과까지 지우면 취소나 실패 뒤에 빈 화면만 남는다. 추천 DOM 교체는
// 새 응답을 받은 renderResponse가 맡는다.
function invalidateExport() {
  youtubeExportGeneration += 1;
  resolveMatchReview(undefined);
  activeYouTubeOperationId = null;
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

  // 검색 URL만 만든다. 행 전체를 링크로 만들지 않아 제목 선택과 스크롤을 막지
  // 않는다. 곡명이 없으면 검색어가 아티스트 하나로 뭉개지므로 명령을 뺀다.
  const open = fragment.querySelector(".track-open");
  const searchUrl = youtubeMusicSearchUrl(track);
  if (searchUrl) {
    open.href = searchUrl;
    open.setAttribute("aria-label", youtubeMusicSearchLabel(track));
  } else {
    open.remove();
  }
  return fragment;
}

const bucketTabId = (name) => `bucketTab-${name}`;
// tabpanel은 #results 하나로 고정하고 내용만 갈아 끼운다. 선택된 패널만 DOM에
// 두면 나머지 탭의 aria-controls가 존재하지 않는 id를 가리키게 된다.
const BUCKET_PANEL_ID = "results";

function renderBucketPanel(bucket) {
  const fragment = bucketTemplate.content.cloneNode(true);
  const section = fragment.querySelector(".bucket");
  section.dataset.bucket = bucket.name;
  // 패널은 하나뿐이므로 어느 탭이 이 내용을 설명하는지만 갱신한다.
  results.setAttribute("aria-labelledby", bucketTabId(bucket.name));

  const exportButton = fragment.querySelector(".export-button");
  if (bucket.tracks.length === 0) {
    // 실행은 됐지만 결과가 없는 방향. 내보낼 곡이 없으니 명령도 두지 않는다.
    exportButton.remove();
    fragment.querySelector(".bucket-empty").hidden = false;
  } else {
    exportButton.addEventListener("click", () => {
      // 매칭 검토를 닫은 뒤 이 버튼으로 포커스를 돌려주기 위해 기억한다.
      matchReviewOpener = exportButton;
      exportBucket(bucket.name, bucket.tracks);
    });
    const list = fragment.querySelector(".track-list");
    bucket.tracks.forEach((track, index) =>
      list.append(renderTrack(track, index)),
    );
  }

  results.replaceChildren(fragment);
  // 탭을 옮겨 새로 그린 버튼도 진행 중인 내보내기 상태를 물려받아야 한다.
  applyExportDisabled();
}

function selectBucket(index, { focusTab = false } = {}) {
  if (index < 0 || index >= renderedBuckets.length) {
    return;
  }

  selectedBucketIndex = index;
  [...bucketTabs.children].forEach((tab, position) => {
    const selected = position === index;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (selected && focusTab) {
      tab.focus();
    }
  });
  renderBucketPanel(renderedBuckets[index]);
}

function renderBucketTabs(buckets) {
  bucketTabs.replaceChildren(
    ...buckets.map((bucket, index) => {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = "bucket-tab";
      tab.id = bucketTabId(bucket.name);
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-controls", BUCKET_PANEL_ID);
      tab.setAttribute("aria-selected", "false");
      tab.tabIndex = -1;

      const count = document.createElement("span");
      count.className = "bucket-tab-count";
      count.textContent = String(bucket.tracks.length);
      tab.append(bucket.label, count);
      tab.addEventListener("click", () => selectBucket(index));
      return tab;
    }),
  );
  bucketTabs.hidden = buckets.length === 0;
}

// tablist 안에서는 방향키가 탭을 옮긴다. Tab 키는 목록 밖으로 나간다.
bucketTabs.addEventListener("keydown", (event) => {
  if (renderedBuckets.length === 0) {
    return;
  }

  const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
  const target =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? renderedBuckets.length - 1
        : step === undefined
          ? null
          : (selectedBucketIndex + step + renderedBuckets.length) %
            renderedBuckets.length;

  if (target === null) {
    return;
  }
  event.preventDefault();
  selectBucket(target, { focusTab: true });
});

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
  // 이전 미리듣기를 멈추고 나서 새 기준 곡을 그린다.
  resetSeedMedia();
  seedTitle.textContent = payload.track_name || "기준 곡 없음";
  seedArtist.textContent = payload.artist || "";
  renderSeedArtwork(payload);
  renderSeedPreview(payload, apiBaseUrl);
  seedSection.hidden = false;

  renderedBuckets = executedBuckets(payload.result);
  renderBucketTabs(renderedBuckets);
  if (renderedBuckets.length === 0) {
    results.replaceChildren();
    results.removeAttribute("aria-labelledby");
    return 0;
  }

  selectBucket(defaultBucketIndex(renderedBuckets));
  // 화면에는 한 탭만 그리므로 총 곡 수는 응답에서 센다.
  return totalTrackCount(renderedBuckets);
}

function setExportButtonsDisabled(disabled) {
  exportInFlight = disabled;
  applyExportDisabled();
}

// 새 추천을 기다리는 동안에도 막는다. 화면에 남은 결과는 이전 곡의 것이고,
// 그것을 내보내면 새 결과 위로 이전 곡의 매칭 검토 창이 열린다.
function applyExportDisabled() {
  const disabled = exportInFlight || requestPending;
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
  // close()만 부르면 close 리스너가 대기 중인 검토를 취소(null)로 정리하고,
  // exportBucket이 방금 그린 이 상태를 "취소됨"으로 덮어쓴다. undefined로
  // 끝내면 exportBucket이 아무것도 다시 그리지 않아 여기서 그린 상태가 남는다.
  if (state.status !== "reviewing" && youtubeMatchReview.open) {
    resolveMatchReview(undefined);
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
  // 포커스 제한, Escape, 배경 inert는 native dialog가 처리한다.
  youtubeMatchReview.showModal();
  youtubeMatchConfirm.focus();
  return new Promise((resolve) => {
    pendingMatchReview = resolve;
  });
}

function resolveMatchReview(value) {
  const resolve = pendingMatchReview;
  pendingMatchReview = null;
  if (youtubeMatchReview.open) {
    youtubeMatchReview.close();
  }
  resolve?.(value);
}

// Escape로 브라우저가 닫은 경우에도 대기 중인 검토를 취소로 정리하고, 내보내기를
// 시작한 버튼으로 포커스를 돌려준다.
youtubeMatchReview.addEventListener("close", () => {
  if (pendingMatchReview) {
    const resolve = pendingMatchReview;
    pendingMatchReview = null;
    resolve(null);
  }

  // 확정 경로에서는 플레이리스트 생성이 이어져 내보내기 버튼이 비활성인 채로
  // 남는다. disabled 요소는 포커스를 못 받아 <body>로 떨어지므로 선택된 탭으로
  // 되돌린다. 취소 경로에서 버튼이 다시 살아나는 것은 close 이벤트(매크로태스크)
  // 앞에 상태 갱신(마이크로태스크)이 끼어들기 때문이며, 그 순서에 기대지 않는다.
  const opener = matchReviewOpener;
  matchReviewOpener = null;
  const usableOpener = opener?.isConnected && !opener.disabled ? opener : null;
  (usableOpener || bucketTabs.children[selectedBucketIndex])?.focus();
});

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
  if (requestPending || exportInFlight) return;
  if (!currentRecommendation) {
    // 백엔드에 닿아 본 적 없는 조건이라 연결 배지는 건드리지 않는다.
    setStatus("먼저 추천 결과를 요청하세요.", true);
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

async function requestRecommendations(
  apiBaseUrl,
  query,
  accessToken,
  controller,
) {
  const timeoutId = setTimeout(
    () => controller.abort(timeoutReason()),
    REQUEST_TIMEOUT_MS,
  );

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

// 요청 중에는 제출 버튼이 취소 명령이 된다. disabled로 막아 두면 최대 90초 동안
// 사용자가 할 수 있는 일이 없다.
function setRequestPending(pending) {
  requestPending = pending;
  submitButton.textContent = pending ? "취소" : "추천 요청";
  submitButton.classList.toggle("cancel-button", pending);
  // 요청 중 버튼은 폼 제출이 아니라 취소 명령이다. 사용자가 대기 중 검색어나
  // 백엔드 주소를 비워도 required/type 검증이 submit 이벤트를 막지 않게 한다.
  form.noValidate = pending;
  currentTrackButton.disabled = pending;
  results.setAttribute("aria-busy", String(pending));
  results.classList.toggle("is-loading", pending);
  applyExportDisabled();
}

// 첫 실행에서 우리가 연 설정만, 첫 추천이 성공한 뒤에 접는다. 토큰 입력 중에
// 접으면 편집 중인 값과 포커스를 빼앗는다.
function closeOnboardingSettings() {
  if (settingsUserToggled || !settingsAutoOpened || !backendAccessTokenInput.value.trim()) {
    return;
  }
  settingsAutoOpened = false;
  settingsPanel.open = false;
}

async function runRecommendation(query, loadingMessage) {
  // A manual search invalidates any earlier current-track lookup, even after
  // this request has completed or been cancelled.
  recommendationIntent += 1;
  activeRequest?.abort();
  invalidateExport();
  const controller = new AbortController();
  activeRequest = controller;
  // 이 요청이 아직 주인일 때만 화면과 상태를 건드린다. 취소당한 이전 요청의
  // catch와 finally가 최신 요청의 화면을 되돌리면 안 된다.
  const isCurrent = () => activeRequest === controller;
  setRequestPending(true);

  try {
    const apiBaseUrl = normalizeApiBaseUrl(apiBaseUrlInput.value);
    const accessToken = backendAccessTokenInput.value.trim();
    if (!query) {
      throw new PreflightError("검색어를 입력하세요.");
    }

    if (requiresBackendAccessToken(apiBaseUrl) && !accessToken) {
      openSettings();
      throw new PreflightError("배포 백엔드 사용에는 팀 백엔드 토큰이 필요합니다.");
    }

    await Promise.all([
      storeApiBaseUrl(apiBaseUrl),
      accessToken ? storeBackendAccessToken(accessToken) : Promise.resolve(),
    ]);
    if (!isCurrent()) return;
    controller.signal.throwIfAborted();
    // 이전 결과가 있으면 흐리게 유지한다. 전체 스켈레톤은 첫 요청에만 쓴다.
    showView(currentRecommendation ? "none" : "loading");
    setState(
      "loading",
      loadingMessage ||
        "백엔드에서 추천 결과를 가져오는 중입니다. 최대 90초까지 걸릴 수 있습니다.",
    );

    const payload = await requestRecommendations(
      apiBaseUrl,
      query,
      accessToken,
      controller,
    );
    if (!isCurrent()) {
      return;
    }
    const resultCount = renderResponse(payload, apiBaseUrl);
    // 응답을 받았으면 첫 진입 안내를 띄우지 않는다. 곡 0개와 아직 아무것도
    // 요청하지 않음은 다른 상태다. 버킷 탭과 방향별 빈 안내가 이미 결과를
    // 설명하는데 그 위에 온보딩 범례를 겹치면 화면이 두 겹이 된다.
    showView("none");
    await rememberQuery(query);
    if (!isCurrent()) return;
    controller.signal.throwIfAborted();
    closeOnboardingSettings();

    setState(
      "success",
      resultCount === 0
        ? "추천 결과가 없습니다. 다른 검색어를 시도해 보세요."
        : `추천 결과 ${resultCount}곡을 받았습니다.`,
    );
  } catch (error) {
    if (!isCurrent()) {
      return;
    }
    if (error instanceof PreflightError) {
      // 요청을 보내지 않았으므로 연결 상태는 알 수 없다. 배지를 그대로 두고
      // 본문에만 알린다. 여기서 "연결 실패"를 띄우면 입력값 문제를 서버 장애로
      // 오인해 엉뚱한 곳을 파게 된다.
      showView(currentRecommendation ? "none" : "empty");
      setStatus(error.message, true);
      return;
    }
    // 실패해도 이전 결과는 남긴다. 지울 결과가 없을 때만 안내 화면으로 돌아간다.
    showView(currentRecommendation ? "none" : "empty");
    if (error?.name === "AbortError") {
      // 취소는 서버 상태에 대한 정보가 아니므로 연결 배지를 실패로 바꾸지 않는다.
      setState(
        currentRecommendation ? "success" : "idle",
        requestErrorMessage(error),
      );
      return;
    }
    setState("error", requestErrorMessage(error));
  } finally {
    if (isCurrent()) {
      activeRequest = null;
      setRequestPending(false);
    }
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();

  if (activeRequest) {
    // 인자 없는 abort는 기본 AbortError를 남긴다. 그것이 사용자 취소의 표식이다.
    activeRequest.abort();
    return;
  }
  void runRecommendation(queryInput.value.trim());
});

currentTrackButton.addEventListener("click", async () => {
  const intent = ++recommendationIntent;
  const isCurrent = () => intent === recommendationIntent;
  currentTrackButton.disabled = true;
  let query = null;

  try {
    const track = await readCurrentTrack();
    if (!isCurrent()) return;

    if (!track) {
      setStatus("YouTube Music에서 재생 중인 곡을 찾을 수 없습니다.", true);
      return;
    }

    query = track.artist ? `${track.artist} - ${track.title}` : track.title;
    // 검색어에 남겨 두면 사용자가 그대로 고쳐서 다시 요청할 수 있다.
    queryInput.value = query;
  } catch (error) {
    if (!isCurrent()) return;
    console.error("Failed to read current track:", error);

    // 곡을 못 읽었을 뿐이므로 기존 추천 결과는 그대로 둔다.
    setStatus(
      error instanceof NoMusicTabError
        ? "YouTube Music 탭을 열어 두면 재생 중인 곡을 가져올 수 있습니다."
        : "현재 곡 정보를 가져오는 데 실패했습니다.",
      true,
    );
    return;
  } finally {
    if (isCurrent()) currentTrackButton.disabled = requestPending;
  }

  // 어떤 곡을 읽었는지 상태 영역이 대신 알린다. 콜아웃을 따로 두지 않는다.
  await runRecommendation(query, `${query} 추천을 찾는 중입니다.`);
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
    openSettingsForOnboarding();

    if (resolved.shouldPersist) {
      await storeApiBaseUrl(resolved.apiBaseUrl);
    }
    queryInput.focus();
    // 복원한 검색어는 전체 선택해 바로 덮어쓸 수 있게 한다.
    queryInput.select();
  })
  .catch(() => {
    apiBaseUrlInput.value = DEFAULT_API_BASE_URL;
    openSettingsForOnboarding();
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

let eqBusy = false;
let eqViewVersion = 0;
let eqModeEdited = false;
let lastEqState = null;
let eqFailure = null;
let eqFailureKey = null;
eqModes.forEach((input) => input.addEventListener("change", () => {
  eqModeEdited = true;
  eqFailure = null;
  renderEqState(lastEqState);
}));

function eqStateKey(state) {
  const status = state?.status || "inactive";
  return JSON.stringify([
    status, Boolean(state?.active), Boolean(state?.capturing ?? state?.active),
    state?.tabId ?? null, status === "inactive" ? null : state?.mode ?? null,
    state?.track?.videoId ?? null, state?.track?.title ?? null, state?.track?.artist ?? null,
  ]);
}

// A failure is anchored to the state it left behind: repeated snapshots of that
// state are not a recovery, only a move off the anchor is. A pushed error
// carries no state of its own, so it anchors to the first state observed after
// it -- the transition the failure itself caused must not erase its diagnosis.
function showEqFailure(message, anchor = eqStateKey(lastEqState)) {
  eqViewVersion += 1;
  eqFailure = message;
  eqFailureKey = anchor;
  eqTestStatus.textContent = message;
}

function renderEqState(state) {
  if (state?.status === "error") {
    showEqFailure(eqStatusText(state), null);
    return;
  }
  eqViewVersion += 1;
  if (eqFailure) {
    if (eqFailureKey === null) eqFailureKey = eqStateKey(state);
    else if (eqStateKey(state) !== eqFailureKey) eqFailure = null;
  }
  lastEqState = state;
  eqTestStatus.textContent = eqFailure || eqStatusText(state);
  eqTrack.textContent = state?.track
    ? [state.track.title, state.track.artist].filter(Boolean).join(" - ") : "";
  eqTrack.hidden = !eqTrack.textContent;
  if (!eqModeEdited && ["auto", "test"].includes(state?.mode) &&
      (state.active || state.status === "awaiting_activation")) {
    for (const input of eqModes) input.checked = input.value === state.mode;
  }
}

async function refreshEqState() {
  if (eqBusy) return;
  const version = eqViewVersion;
  try {
    const state = await getEqState();
    if (version === eqViewVersion) renderEqState(state);
  } catch {
    if (version === eqViewVersion && !eqFailure) eqTestStatus.textContent = "EQ 상태를 확인하지 못했습니다.";
  }
}

chrome.runtime.onMessage.addListener((message) => {
  if (message.target === "eq-ui" && message.type === "EQ_STATE_UPDATED") renderEqState(message.state);
});
void refreshEqState();
// Also expire a pending toolbar request while the panel stays open.
setInterval(() => { if (!document.hidden) void refreshEqState(); }, 3_000);

function setEqBusy(busy) {
  eqBusy = busy;
  if (busy) eqFailure = null;
  eqViewVersion += 1;
  eqTestButton.disabled = busy;
  eqStopButton.disabled = busy;
  eqModes.forEach((input) => { input.disabled = busy; });
}

eqTestButton.addEventListener("click", async () => {
  const mode = document.querySelector('input[name="eqMode"]:checked').value;
  setEqBusy(true);

  try {
    eqTestStatus.textContent = "EQ 적용 중...";

    const state = await startEq(mode);
    eqModeEdited = false;
    renderEqState(state);
  } catch (error) {
    console.error("Failed to apply EQ:", error);

    showEqFailure(`EQ 적용 실패: ${
      error?.message || "알 수 없는 오류"
    }`);
  } finally {
    setEqBusy(false);
  }
});

eqStopButton.addEventListener("click", async () => {
  setEqBusy(true);

  try {
    renderEqState(await stopEq());
  } catch (error) {
    console.error("Failed to stop EQ:", error);

    showEqFailure(`EQ 해제 실패: ${
      error?.message || "알 수 없는 오류"
    }`);
  } finally {
    setEqBusy(false);
  }
});
