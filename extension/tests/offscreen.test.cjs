const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const OFFSCREEN_SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "offscreen.js"),
  "utf8",
);

const DEFAULT_Q = 1.4;

function audioParam(initial) {
  return {
    value: initial,
    cancelScheduledValues() {},
    setValueAtTime(value) { this.value = value; },
    setTargetAtTime(value) {
      this.value = value;
    },
  };
}

/** 실제 Web Audio 그래프 대신 연결 관계와 파라미터만 기록하는 대역. */
function fakeAudioContext(graph, options) {
  const context = {
    state: "suspended",
    sampleRate: 48000,
    addEventListener() {},
    removeEventListener() {},
    currentTime: 0,
    destination: { id: "destination", connect() {}, disconnect() {} },
    createGain() {
      const node = { id: "preamp", gain: audioParam(1), connect: null, disconnect: null };
      node.connect = connectTo(node);
      node.disconnect = disconnectFrom(node);
      graph.preamp = node;
      return node;
    },
    createBiquadFilter() {
      const node = {
        id: `filter-${graph.created.length}`,
        type: "",
        frequency: { value: 0 },
        Q: audioParam(0),
        gain: audioParam(0),
        getFrequencyResponse(_frequencies, magnitude) { magnitude.fill(1); },
        connect: null,
        disconnect: null,
      };
      node.connect = connectTo(node);
      node.disconnect = disconnectFrom(node);
      graph.created.push(node);
      return node;
    },
    createMediaStreamSource() {
      if (options.sourceFailure) throw new Error("source failed");
      const node = { id: "source", connect: null, disconnect: null };
      node.connect = connectTo(node);
      node.disconnect = disconnectFrom(node);
      return node;
    },
    async resume() {
      if (options.resumeFailure) throw new Error("resume failed");
      this.state = "running";
      graph.resumed = true;
    },
    async close() {
      this.state = "closed";
      graph.closed = true;
    },
  };

  function connectTo(from) {
    return (to) => {
      graph.edges.push([from.id, to.id]);
    };
  }
  function disconnectFrom(from) {
    return () => {
      graph.edges = graph.edges.filter(([source]) => source !== from.id);
      graph.disconnected.push(from.id);
    };
  }

  return context;
}

async function loadOffscreen(options = {}) {
  const graph = { created: [], edges: [], disconnected: [], preamp: null };
  const timers = new Map();
  const intervals = new Map();
  const streams = [];
  const updates = [];
  let nextTimer = 0;
  const context = vm.createContext({
    chrome: { runtime: {
      onMessage: { addListener() {} },
      async sendMessage(message) {
        if (message.type === "READ_EQ_TRACK") {
          return options.readTrack ? options.readTrack() : { ok: true, track: options.track || null };
        }
        updates.push(message.state);
        return { ok: true };
      },
    } },
    console: { log() {}, error() {} },
    AudioContext: function () {
      return fakeAudioContext(graph, options);
    },
    navigator: {
      mediaDevices: {
        async getUserMedia() {
          if (options.mediaFailure) throw new Error("media failed");
          const track = {
            readyState: "live", ended: null,
            addEventListener(_name, callback) { this.ended = callback; },
            stop() { this.readyState = "ended"; },
          };
          const stream = { track, getAudioTracks: () => [track], getTracks: () => [track] };
          streams.push(stream);
          return stream;
        },
      },
    },
    Promise,
    AbortController,
    setTimeout(fn, ms) { const id = ++nextTimer; timers.set(id, { fn, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval(fn) { const id = ++nextTimer; intervals.set(id, fn); return id; },
    clearInterval(id) { intervals.delete(id); },
  });
  for (const file of ["eqPresets.js", "eqProvider.js"]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "scripts", file), "utf8"), context);
  }
  if (options.provider) context.SideBEqProvider.getPreset = options.provider;
  vm.runInContext(OFFSCREEN_SOURCE, context);
  return { context, graph, timers, intervals, streams, updates };
}

function preset(bands, preamp = 0) {
  return { preamp, bands };
}

async function startWith(bands) {
  const harness = await loadOffscreen();
  await harness.context.startEq({
    tabId: 1,
    streamId: "stream-id",
    preset: preset(bands),
  });
  return harness;
}

function activeFilters(graph) {
  // 연결이 끊긴 노드는 그래프에서 빠진다. 남아 있는 필터만 소리에 영향을 준다.
  const connected = new Set(graph.edges.map(([source]) => source));
  return graph.created.filter((node) => connected.has(node.id));
}

test("같은 대역 구성이면 필터를 다시 만들지 않는다", async () => {
  const { context, graph } = await startWith([
    { frequency: 60, gain: 6 },
    { frequency: 1000, gain: -3 },
  ]);
  const originalFilters = activeFilters(graph);

  context.updateEq(preset([
    { frequency: 60, gain: 2 },
    { frequency: 1000, gain: 1 },
  ]));

  assert.deepEqual(activeFilters(graph), originalFilters, "필터를 새로 만들면 안 된다.");
  assert.equal(originalFilters[0].gain.value, 2);
  assert.equal(originalFilters[1].gain.value, 1);
});

test("대역이 사라지면 그 필터도 그래프에서 빠진다", async () => {
  const { context, graph } = await startWith([
    { frequency: 31, gain: 12 },
    { frequency: 62, gain: 12 },
    { frequency: 1000, gain: -8 },
  ]);

  context.updateEq(preset([{ frequency: 1000, gain: -8 }]));

  const remaining = activeFilters(graph).map((node) => node.frequency.value);
  assert.deepEqual(remaining, [1000], `저역 필터가 남았다: ${remaining}`);
});

test("새 주파수가 생기면 필터가 만들어진다", async () => {
  const { context, graph } = await startWith([{ frequency: 1000, gain: -8 }]);

  context.updateEq(preset([
    { frequency: 1000, gain: -8 },
    { frequency: 8000, gain: 5 },
  ]));

  const frequencies = activeFilters(graph).map((node) => node.frequency.value);
  assert.deepEqual(frequencies, [1000, 8000]);
});

test("q를 생략하면 기본값으로 돌아간다", async () => {
  const { context, graph } = await startWith([{ frequency: 60, gain: 6, q: 3 }]);
  assert.equal(activeFilters(graph)[0].Q.value, 3);

  context.updateEq(preset([{ frequency: 60, gain: 6 }]));

  assert.equal(
    activeFilters(graph)[0].Q.value,
    DEFAULT_Q,
    "이전 프리셋의 Q가 남으면 같은 preset이 다른 소리를 낸다.",
  );
});

test("재구축한 필터도 preamp에서 destination까지 직렬로 이어진다", async () => {
  const { context, graph } = await startWith([{ frequency: 1000, gain: -8 }]);

  context.updateEq(preset([
    { frequency: 60, gain: 6 },
    { frequency: 8000, gain: 5 },
  ]));

  const active = activeFilters(graph);
  assert.equal(active.length, 2);
  const edges = graph.edges.map(([from, to]) => `${from}->${to}`);
  assert.ok(edges.includes(`preamp->${active[0].id}`), `preamp 연결 없음: ${edges}`);
  assert.ok(
    edges.includes(`${active[0].id}->${active[1].id}`),
    `필터 간 연결 없음: ${edges}`,
  );
  assert.ok(
    edges.includes(`${active[1].id}->destination`),
    `destination 연결 없음: ${edges}`,
  );
});

const settle = () => new Promise(setImmediate);
const song = (title) => ({ title, artist: "Artist", videoId: title, url: `https://music.youtube.com/watch?v=${title}` });
const cut = (frequency) => preset([{ frequency, gain: -6 }]);

async function startAuto(options = {}) {
  const h = await loadOffscreen(options);
  await h.context.startEq({ streamId: "stream-id", tabId: 1, mode: "auto" });
  await settle();
  return h;
}

test("without the future AI adapter, auto EQ explicitly keeps the original audio", async () => {
  const h = await startAuto({ track: song("A") });
  assert.equal(h.context.getState().active, true);
  assert.equal(h.context.getState().status, "unavailable");
  assert.equal(activeFilters(h.graph).length, 0);
  assert.equal(h.graph.preamp.gain.value, 1);
});

test("one AI request per observed track, independent of repeated polling", async () => {
  let requests = 0;
  const h = await startAuto({ track: song("A"), provider: async () => { requests++; return cut(1000); } });
  for (const poll of h.intervals.values()) poll();
  await settle();
  assert.equal(requests, 1);
  assert.equal(h.context.getState().status, "applied");
  assert.equal(activeFilters(h.graph)[0].frequency.value, 1000);
});

test("changing songs clears the old filters before the new AI result arrives", async () => {
  let resolveB;
  const options = { track: song("A"), provider: async (track) => track.title === "A" ? cut(1000) : new Promise((resolve) => { resolveB = resolve; }) };
  const h = await startAuto(options);
  options.track = song("B");
  for (const poll of h.intervals.values()) poll();
  await settle();
  assert.equal(h.context.getState().status, "analyzing");
  assert.equal(activeFilters(h.graph).length, 0);
  resolveB(cut(8000));
  await settle();
  assert.equal(activeFilters(h.graph)[0].frequency.value, 8000);
});

test("late AI results from an earlier song cannot replace the current song", async () => {
  let resolveA;
  let signalA;
  const options = { track: song("A"), provider: (track, { signal }) => {
    if (track.title === "A") { signalA = signal; return new Promise((resolve) => { resolveA = resolve; }); }
    return Promise.resolve(cut(8000));
  } };
  const h = await startAuto(options);
  options.track = song("B");
  for (const poll of h.intervals.values()) poll();
  await settle();
  assert.equal(signalA.aborted, true);
  resolveA(cut(1000));
  await settle();
  assert.equal(h.context.getState().track.title, "B");
  assert.equal(activeFilters(h.graph)[0].frequency.value, 8000);
});

test("rechecks the song immediately before applying even before the next poll", async () => {
  let finish;
  const options = { track: song("A"), provider: (track) => track.title === "A"
    ? new Promise((resolve) => { finish = resolve; }) : Promise.resolve(cut(8000)) };
  const h = await startAuto(options);
  options.track = song("B");
  finish(cut(1000));
  await settle();
  assert.equal(h.context.getState().track.title, "B");
  assert.equal(activeFilters(h.graph)[0].frequency.value, 8000);
});

for (const [label, provider] of [
  ["failure", async () => { throw new Error("provider offline"); }],
  ["malformed result", async () => ({ bands: [{ frequency: 1000, gain: NaN }] })],
  ["out-of-range gain", async () => ({ bands: [{ frequency: 1000, gain: 100 }] })],
  ["duplicate bands", async () => ({ bands: [{ frequency: 1000, gain: -2 }, { frequency: 1000, gain: -4 }] })],
]) {
  test(`AI ${label} falls back to original audio`, async () => {
    const h = await startAuto({ track: song("A"), provider });
    assert.equal(h.context.getState().status, "unavailable");
    assert.equal(h.context.getState().active, true);
    assert.equal(activeFilters(h.graph).length, 0);
    assert.equal(h.graph.preamp.gain.value, 1);
  });
}

test("AI timeout recovers even if the adapter ignores AbortSignal", async () => {
  let signal;
  const h = await startAuto({ track: song("A"), provider: (_track, options) => {
    signal = options.signal;
    return new Promise(() => {});
  } });
  [...h.timers.values()].find((timer) => timer.ms === 10_000).fn();
  await settle();
  assert.equal(signal.aborted, true);
  assert.equal(h.context.getState().status, "unavailable");
  assert.equal(activeFilters(h.graph).length, 0);
});

test("switching to test mode cancels AI and changing back removes its filters", async () => {
  let finish;
  const h = await startAuto({ track: song("A"), provider: () => new Promise((resolve) => { finish = resolve; }) });
  await h.context.setEqMode("test");
  finish(cut(8000));
  await settle();
  assert.equal(h.context.getState().mode, "test");
  assert.equal(activeFilters(h.graph)[0].frequency.value, 1000);
  assert.equal(h.intervals.size, 0);
  await h.context.setEqMode("auto");
  assert.equal(activeFilters(h.graph).length, 0);
});

test("stop releases audio capture, polling and pending AI without later resurrection", async () => {
  let finish;
  const h = await startAuto({ track: song("A"), provider: () => new Promise((resolve) => { finish = resolve; }) });
  await h.context.stopEq();
  finish(cut(1000));
  await settle();
  assert.equal(h.context.getState().active, false);
  assert.equal(h.context.getState().status, "inactive");
  assert.equal(h.streams[0].track.readyState, "ended");
  assert.equal(h.graph.closed, true);
  assert.equal(h.intervals.size, 0);
  assert.equal(h.graph.edges.length, 0);
});

for (const failure of ["resumeFailure", "sourceFailure"]) {
  test(`startup ${failure} does not leave the music tab captured and silent`, async () => {
    const h = await loadOffscreen({ [failure]: true });
    await assert.rejects(h.context.startEq({ streamId: "stream", tabId: 1, mode: "auto" }), /failed/);
    assert.equal(h.streams[0].track.readyState, "ended");
    assert.equal(h.context.getState().capturing, false);
    assert.equal(h.context.getState().status, "inactive");
  });
}

test("an old stream's ended event cannot clean up a replacement stream", async () => {
  const h = await startAuto();
  const oldEnded = h.streams[0].track.ended;
  await h.context.startEq({ streamId: "new", tabId: 2, mode: "test" });
  oldEnded();
  await settle();
  assert.equal(h.context.getState().tabId, 2);
  assert.equal(h.context.getState().active, true);
  assert.equal(h.streams[1].track.readyState, "live");
});

test("missing metadata removes a previously applied AI preset", async () => {
  const options = { track: song("A"), provider: async () => cut(1000) };
  const h = await startAuto(options);
  options.track = null;
  for (const poll of h.intervals.values()) poll();
  await settle();
  assert.equal(h.context.getState().status, "waiting_track");
  assert.equal(activeFilters(h.graph).length, 0);
});
