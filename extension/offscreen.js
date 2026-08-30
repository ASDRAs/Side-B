const DEFAULT_Q = 1.4;
const TRACK_POLL_MS = 2_000;
const AI_TIMEOUT_MS = 10_000;

let audioContext = null;
let mediaStream = null;
let sourceNode = null;
let preampNode = null;
let filterNodes = [];
let currentTabId = null;
let currentMode = "auto";
let currentTrack = null;
let presetStatus = "inactive";
let automation = null;
let commandQueue = Promise.resolve();

function dbToGain(db) {
  return 10 ** (db / 20);
}

function createFilter(context, band) {
  const filter = context.createBiquadFilter();

  filter.type = "peaking";
  filter.frequency.value = band.frequency;
  filter.Q.value = band.q ?? DEFAULT_Q;
  filter.gain.value = band.gain;

  return {
    frequency: band.frequency,
    node: filter,
  };
}

function connectEqGraph() {
  sourceNode.connect(preampNode);

  let currentNode = preampNode;

  for (const filter of filterNodes) {
    currentNode.connect(filter.node);
    currentNode = filter.node;
  }

  currentNode.connect(audioContext.destination);
}

async function createTabMediaStream(streamId) {
  return navigator.mediaDevices.getUserMedia({
    audio: {
      mandatory: {
        chromeMediaSource: "tab",
        chromeMediaSourceId: streamId,
      },
    },
    video: false,
  });
}

async function startEq({ streamId, tabId, preset, mode = "manual" }) {
  preset = SideBEqPresets.validate(preset ||
    (mode === "test" ? SideBEqPresets.test() : SideBEqPresets.flat()));
  await cleanupEq();
  try {
    mediaStream = await createTabMediaStream(streamId);
    const [audioTrack] = mediaStream.getAudioTracks();
    if (!audioTrack || audioTrack.readyState === "ended") throw new Error("오디오 스트림이 없습니다.");
    audioContext = new AudioContext();
    await audioContext.resume();
    if (audioContext.state !== "running") throw new Error("오디오 출력을 시작하지 못했습니다.");
    sourceNode = audioContext.createMediaStreamSource(mediaStream);
    preampNode = audioContext.createGain();
    preampNode.gain.value = dbToGain(headroomPreamp(preset));
    filterNodes = preset.bands.map((band) => createFilter(audioContext, band));
    connectEqGraph();
    currentTabId = tabId;

    const capturedStream = mediaStream;
    audioTrack.addEventListener("ended", () => {
      queueCommand(async () => {
        if (mediaStream === capturedStream) await stopEq();
      }).catch((error) => console.error("Failed to clean up ended stream:", error));
    });
    audioContext.addEventListener("statechange", publishState);
    if (mode !== "manual") return await setEqMode(mode);
    currentMode = mode;
    presetStatus = "applied";
    return publishState();
  } catch (error) {
    // A failed graph must release tabCapture or the original tab remains silent.
    await cleanupEq();
    publishState();
    throw error;
  }
}

function headroomPreamp(preset) {
  if (!preset.bands.some((band) => band.gain > 0)) return preset.preamp;
  const upper = Math.min(20000, audioContext.sampleRate / 2 - 1);
  const frequencies = Float32Array.from({ length: 1024 }, (_, i) => 20 * (upper / 20) ** (i / 1023));
  const combined = new Float32Array(frequencies.length);
  const magnitude = new Float32Array(frequencies.length);
  const phase = new Float32Array(frequencies.length);
  for (const band of preset.bands) {
    const { node } = createFilter(audioContext, band);
    node.getFrequencyResponse(frequencies, magnitude, phase);
    for (let i = 0; i < combined.length; i++) combined[i] += 20 * Math.log10(magnitude[i]);
    node.disconnect();
  }
  // Cascaded boosts overlap. Reserve headroom for the measured total response,
  // not just the largest band; the small margin covers sampling between points.
  return Math.min(preset.preamp, -Math.max(0, Math.max(...combined) + 0.5));
}

function presetMatchesGraph(preset) {
  const bands = preset.bands ?? [];
  if (bands.length !== filterNodes.length) {
    return false;
  }
  return bands.every((band, index) => filterNodes[index].frequency === band.frequency);
}

// 대역 구성이 그대로일 때만 값을 미끄러뜨린다. 구성이 바뀌면 필터를 다시
// 만든다. 예전에는 일치하는 주파수만 갱신해서, 새 프리셋에 없는 필터가 그대로
// 남고 새 주파수는 생기지 않았다. 곡별 EQ를 붙이면 이전 곡의 대역이 섞인다.
function rebuildFilters(preset) {
  for (const filter of filterNodes) {
    filter.node.disconnect();
  }
  preampNode.disconnect();

  filterNodes = (preset.bands ?? []).map((band) =>
    createFilter(audioContext, band),
  );

  let currentNode = preampNode;
  for (const filter of filterNodes) {
    currentNode.connect(filter.node);
    currentNode = filter.node;
  }
  currentNode.connect(audioContext.destination);
}

function updateEq(preset) {
  if (!audioContext || !preampNode) {
    throw new Error("현재 실행 중인 EQ가 없습니다.");
  }
  preset = SideBEqPresets.validate(preset);

  const now = audioContext.currentTime;
  const targetGain = dbToGain(headroomPreamp(preset));
  preampNode.gain.cancelScheduledValues(now);
  if (targetGain < preampNode.gain.value) preampNode.gain.setValueAtTime(targetGain, now);
  else preampNode.gain.setTargetAtTime(targetGain, now, 0.02);

  if (!presetMatchesGraph(preset)) {
    rebuildFilters(preset);
    console.log("EQ rebuilt:", preset);
    return {
      ok: true,
      active: true,
      tabId: currentTabId,
    };
  }

  for (const band of preset.bands ?? []) {
    const filter = filterNodes.find(
      (item) => item.frequency === band.frequency,
    );

    if (!filter) {
      continue;
    }

    filter.node.gain.cancelScheduledValues(now);
    filter.node.gain.setTargetAtTime(
      band.gain,
      now,
      0.02,
    );

    // q를 생략한 대역은 기본값으로 되돌린다. 이전 프리셋이 남긴 Q가 그대로
    // 유지되면 같은 preset을 보내도 소리가 달라진다.
    filter.node.Q.cancelScheduledValues(now);
    filter.node.Q.setTargetAtTime(
      band.q ?? DEFAULT_Q,
      now,
      0.02,
    );
  }

  console.log("EQ updated:", preset);

  return {
    ok: true,
    active: true,
    tabId: currentTabId,
  };
}

async function cleanupEq() {
  stopAutomation();
  const oldContext = audioContext;
  const oldStream = mediaStream;
  // Detach ownership before awaiting close: ended/statechange events from this
  // stream must never clean up a replacement stream.
  mediaStream = null;
  audioContext = null;
  oldStream?.getTracks().forEach((track) => {
    track.stop();
  });

  sourceNode?.disconnect();
  preampNode?.disconnect();

  for (const filter of filterNodes) {
    filter.node.disconnect();
  }

  if (oldContext && oldContext.state !== "closed") {
    oldContext.removeEventListener("statechange", publishState);
    await oldContext.close();
  }

  audioContext = null;
  mediaStream = null;
  sourceNode = null;
  preampNode = null;
  filterNodes = [];
  currentTabId = null;
  currentTrack = null;
  presetStatus = "inactive";
}

async function stopEq() {
  await cleanupEq();

  console.log("EQ stopped.");

  return publishState();
}

function getState() {
  const capturing = Boolean(mediaStream?.getAudioTracks().some((track) => track.readyState === "live"));
  const active = capturing && audioContext?.state === "running";
  return {
    ok: true,
    active,
    capturing,
    tabId: currentTabId,
    mode: currentMode,
    track: currentTrack,
    status: capturing && !active ? "suspended" : presetStatus,
  };
}

function publishState() {
  const state = getState();
  chrome.runtime.sendMessage({ target: "eq-ui", type: "EQ_STATE_UPDATED", state }).catch(() => {});
  return state;
}

function stopAutomation() {
  if (!automation) return;
  clearInterval(automation.timer);
  automation.controller?.abort();
  automation = null;
}

async function setEqMode(mode) {
  if (!["auto", "test"].includes(mode)) throw new Error("알 수 없는 EQ 모드입니다.");
  if (!audioContext) throw new Error("현재 실행 중인 EQ가 없습니다.");
  await audioContext.resume();
  stopAutomation();
  currentMode = mode;
  currentTrack = null;
  updateEq(mode === "test" ? SideBEqPresets.test() : SideBEqPresets.flat());
  presetStatus = mode === "test" ? "applied" : "waiting_track";
  const state = publishState();
  if (mode === "auto") {
    const session = { key: "", polling: false, controller: null, timer: null };
    automation = session;
    // The offscreen document owns polling, so closing the panel or restarting
    // the service worker does not stop track-change detection.
    session.timer = setInterval(() => { void pollTrack(session); }, TRACK_POLL_MS);
    void pollTrack(session);
  }
  return state;
}

async function readCapturedTrack() {
  let timer;
  const response = await Promise.race([
    chrome.runtime.sendMessage({ target: "background", type: "READ_EQ_TRACK", tabId: currentTabId }),
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Track read timed out")), 5_000); }),
  ]).finally(() => clearTimeout(timer));
  if (!response?.ok) throw new Error("곡 정보를 읽지 못했습니다.");
  return response.track;
}

function acceptTrack(session, track) {
  if (automation !== session) return;
  const key = SideBEqPresets.trackKey(track);
  if (key === session.key) return;
  session.key = key;
  session.controller?.abort();
  currentTrack = key ? track : null;
  // Never carry the previous song's EQ into a newly detected/unknown song.
  updateEq(SideBEqPresets.flat());
  presetStatus = key ? "analyzing" : "waiting_track";
  publishState();
  if (key) {
    const controller = new AbortController();
    session.controller = controller;
    void resolvePreset(session, track, key, controller);
  }
}

async function pollTrack(session) {
  if (automation !== session || session.polling) return;
  session.polling = true;
  try {
    acceptTrack(session, await readCapturedTrack());
  } catch {
    acceptTrack(session, null);
  } finally {
    session.polling = false;
  }
}

async function resolvePreset(session, track, key, controller) {
  const { signal } = controller;
  let rejectAbort;
  const aborted = new Promise((_, reject) => { rejectAbort = () => reject(new Error("EQ analysis cancelled")); });
  signal.addEventListener("abort", rejectAbort, { once: true });
  const timeout = setTimeout(() => controller.abort(), AI_TIMEOUT_MS);
  const stillCurrent = () => automation === session && session.key === key && session.controller === controller;
  try {
    const preset = await Promise.race([
      Promise.resolve().then(() => SideBEqProvider.getPreset(track, { signal })), aborted,
    ]);
    if (!stillCurrent() || signal.aborted) return;
    // Re-read immediately before applying, not just on the 2-second poll tick.
    const latest = await Promise.race([readCapturedTrack(), aborted]);
    if (!stillCurrent() || signal.aborted) return;
    if (SideBEqPresets.trackKey(latest) !== key) {
      acceptTrack(session, latest);
      return;
    }
    updateEq(preset === null ? SideBEqPresets.flat() : SideBEqPresets.validate(preset));
    presetStatus = preset === null ? "unavailable" : "applied";
    publishState();
  } catch {
    if (!stillCurrent()) return;
    updateEq(SideBEqPresets.flat());
    presetStatus = "unavailable";
    publishState();
  } finally {
    clearTimeout(timeout);
    signal.removeEventListener("abort", rejectAbort);
  }
}

function queueCommand(action) {
  const result = commandQueue.then(action);
  commandQueue = result.catch(() => {});
  return result;
}

async function handleMessage(message) {
  switch (message.type) {
    case "START_EQ":
      return startEq(message);

    case "UPDATE_EQ":
      SideBEqPresets.validate(message.preset);
      stopAutomation();
      currentMode = "manual";
      updateEq(message.preset);
      presetStatus = "applied";
      return publishState();

    case "SET_EQ_MODE":
      return setEqMode(message.mode);

    case "STOP_EQ":
      return stopEq();

    case "GET_STATE":
      return getState();

    default:
      throw new Error(`알 수 없는 offscreen 메시지: ${message.type}`);
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.target !== "offscreen") {
    return false;
  }

  (message.type === "GET_STATE" ? handleMessage(message) : queueCommand(() => handleMessage(message)))
    .then(sendResponse)
    .catch((error) => {
      console.error("Offscreen EQ error:", error);

      sendResponse({
        ok: false,
        error: error?.message || "알 수 없는 오류가 발생했습니다.",
      });
    });

  return true;
});
