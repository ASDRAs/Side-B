let audioContext = null;
let mediaStream = null;
let sourceNode = null;
let preampNode = null;
let filterNodes = [];

const DEFAULT_Q = 1.4;

function captureCurrentTabAudio() {
  return new Promise((resolve, reject) => {
    chrome.tabCapture.capture(
      {
        audio: true,
        video: false,
      },
      (stream) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }

        if (!stream) {
          reject(new Error("현재 탭의 오디오를 가져오지 못했습니다."));
          return;
        }

        resolve(stream);
      },
    );
  });
}

function 
(context, band) {
  const filter = context.createBiquadFilter();

  filter.type = "peaking";
  filter.frequency.value = band.frequency;
  filter.Q.value = band.q ?? DEFAULT_Q;
  filter.gain.value = band.gain;

  return filter;
}

function connectEqGraph(source, preamp, filters, destination) {
  let currentNode = source;

  currentNode.connect(preamp);
  currentNode = preamp;

  for (const filter of filters) {
    currentNode.connect(filter);
    currentNode = filter;
  }

  currentNode.connect(destination);
}

export async function startEq(preset) {
  if (audioContext) {
    updateEq(preset);
    return;
  }

  mediaStream = await captureCurrentTabAudio();

  audioContext = new AudioContext();
  await audioContext.resume();

  sourceNode = audioContext.createMediaStreamSource(mediaStream);

  preampNode = audioContext.createGain();
  preampNode.gain.value = dbToGain(preset.preamp ?? 0);

  filterNodes = preset.bands.map((band) =>
    createFilter(audioContext, band),
  );

  connectEqGraph(
    sourceNode,
    preampNode,
    filterNodes,
    audioContext.destination,
  );
}

export function updateEq(preset) {
  if (!audioContext || !preampNode) {
    throw new Error("EQ가 시작되지 않았습니다.");
  }

  if (preset.preamp !== undefined) {
    preampNode.gain.setTargetAtTime(
      dbToGain(preset.preamp),
      audioContext.currentTime,
      0.02,
    );
  }

  for (const band of preset.bands) {
    const filter = filterNodes.find(
      (node) => node.frequency.value === band.frequency,
    );

    if (!filter) {
      continue;
    }

    filter.gain.setTargetAtTime(
      band.gain,
      audioContext.currentTime,
      0.02,
    );
  }
}

export async function stopEq() {
  mediaStream?.getTracks().forEach((track) => track.stop());

  sourceNode?.disconnect();
  preampNode?.disconnect();

  for (const filter of filterNodes) {
    filter.disconnect();
  }

  if (audioContext) {
    await audioContext.close();
  }

  audioContext = null;
  mediaStream = null;
  sourceNode = null;
  preampNode = null;
  filterNodes = [];
}

function dbToGain(db) {
  return 10 ** (db / 20);
}