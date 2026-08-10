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
  results.replaceChildren();
  seedSection.hidden = true;
  rawPanel.hidden = true;
  rawPanel.open = false;
  rawResponse.textContent = "";
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
  fragment.querySelector("h2").textContent = BUCKET_LABELS[bucketName] || bucketName;
  fragment.querySelector(".count").textContent = `${tracks.length}곡`;

  const list = fragment.querySelector(".track-list");
  tracks.forEach((track, index) => list.append(renderTrack(track, index)));
  results.append(fragment);
}

function renderResponse(payload) {
  seedTitle.textContent = payload.track_name || "기준 곡 없음";
  seedArtist.textContent = payload.artist || "";
  seedSection.hidden = false;

  const buckets = payload.result || {};
  Object.keys(BUCKET_LABELS).forEach((name) => renderBucket(name, buckets[name]));

  rawResponse.textContent = JSON.stringify(payload, null, 2);
  rawPanel.hidden = false;
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

readStoredApiBaseUrl()
  .then((storedApiBaseUrl) => {
    apiBaseUrlInput.value = storedApiBaseUrl || DEFAULT_API_BASE_URL;
    queryInput.focus();
  })
  .catch(() => {
    apiBaseUrlInput.value = DEFAULT_API_BASE_URL;
  });
