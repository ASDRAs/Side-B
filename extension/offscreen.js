const DEFAULT_Q = 1.4;

let audioContext = null;
let mediaStream = null;
let sourceNode = null;
let preampNode = null;
let filterNodes = [];
let currentTabId = null;

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

async function startEq({ streamId, tabId, preset }) {
  await cleanupEq();

  mediaStream = await createTabMediaStream(streamId);

  audioContext = new AudioContext();
  await audioContext.resume();

  sourceNode = audioContext.createMediaStreamSource(mediaStream);

  preampNode = audioContext.createGain();
  preampNode.gain.value = dbToGain(preset.preamp ?? 0);

  filterNodes = (preset.bands ?? []).map((band) =>
    createFilter(audioContext, band),
  );

  connectEqGraph();

  currentTabId = tabId;

  const [audioTrack] = mediaStream.getAudioTracks();

  audioTrack?.addEventListener("ended", () => {
    cleanupEq().catch((error) => {
      console.error("Failed to clean up ended stream:", error);
    });
  });

  console.log("EQ started:", {
    tabId,
    preset,
  });

  return {
    ok: true,
    active: true,
    tabId: currentTabId,
  };
}

function updateEq(preset) {
  if (!audioContext || !preampNode) {
    throw new Error("현재 실행 중인 EQ가 없습니다.");
  }

  const now = audioContext.currentTime;

  preampNode.gain.setTargetAtTime(
    dbToGain(preset.preamp ?? 0),
    now,
    0.02,
  );

  for (const band of preset.bands ?? []) {
    const filter = filterNodes.find(
      (item) => item.frequency === band.frequency,
    );

    if (!filter) {
      continue;
    }

    filter.node.gain.setTargetAtTime(
      band.gain,
      now,
      0.02,
    );

    if (band.q !== undefined) {
      filter.node.Q.setTargetAtTime(
        band.q,
        now,
        0.02,
      );
    }
  }

  console.log("EQ updated:", preset);

  return {
    ok: true,
    active: true,
    tabId: currentTabId,
  };
}

async function cleanupEq() {
  mediaStream?.getTracks().forEach((track) => {
    track.stop();
  });

  sourceNode?.disconnect();
  preampNode?.disconnect();

  for (const filter of filterNodes) {
    filter.node.disconnect();
  }

  if (audioContext && audioContext.state !== "closed") {
    await audioContext.close();
  }

  audioContext = null;
  mediaStream = null;
  sourceNode = null;
  preampNode = null;
  filterNodes = [];
  currentTabId = null;
}

async function stopEq() {
  await cleanupEq();

  console.log("EQ stopped.");

  return {
    ok: true,
    active: false,
    tabId: null,
  };
}

function getState() {
  return {
    ok: true,
    active: Boolean(audioContext && mediaStream),
    tabId: currentTabId,
  };
}

async function handleMessage(message) {
  switch (message.type) {
    case "START_EQ":
      return startEq(message);

    case "UPDATE_EQ":
      return updateEq(message.preset);

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

  handleMessage(message)
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