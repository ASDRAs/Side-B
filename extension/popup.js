import { readCurrentTrack } from "./scripts/tab.js";
import { startEq, stopEq } from "./scripts/eq.js";

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
const currentTrackButton = document.querySelector("#currentTrackButton");
const currentTrackResult = document.querySelector("#currentTrackResult");
const currentTrackTitle = document.querySelector("#currentTrackTitle");
const currentTrackArtist = document.querySelector("#currentTrackArtist");
const eqTestButton = document.querySelector("#eqTestButton");
const eqStopButton = document.querySelector("#eqStopButton");
const eqTestStatus = document.querySelector("#eqTestStatus");

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