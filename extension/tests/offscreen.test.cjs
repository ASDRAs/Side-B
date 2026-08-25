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
    setTargetAtTime(value) {
      this.value = value;
    },
  };
}

/** 실제 Web Audio 그래프 대신 연결 관계와 파라미터만 기록하는 대역. */
function fakeAudioContext(graph) {
  const context = {
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
        connect: null,
        disconnect: null,
      };
      node.connect = connectTo(node);
      node.disconnect = disconnectFrom(node);
      graph.created.push(node);
      return node;
    },
    createMediaStreamSource() {
      const node = { id: "source", connect: null, disconnect: null };
      node.connect = connectTo(node);
      node.disconnect = disconnectFrom(node);
      return node;
    },
    async resume() {
      graph.resumed = true;
    },
    async close() {
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

async function loadOffscreen() {
  const graph = { created: [], edges: [], disconnected: [], preamp: null };
  const context = vm.createContext({
    chrome: { runtime: { onMessage: { addListener() {} } } },
    console: { log() {}, error() {} },
    AudioContext: function () {
      return fakeAudioContext(graph);
    },
    navigator: {
      mediaDevices: {
        async getUserMedia() {
          return { getAudioTracks: () => [{ addEventListener() {} }] };
        },
      },
    },
    Promise,
    setTimeout,
    clearTimeout,
  });
  vm.runInContext(OFFSCREEN_SOURCE, context);
  return { context, graph };
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

  context.updateEq(preset([
    { frequency: 60, gain: 2 },
    { frequency: 1000, gain: 1 },
  ]));

  assert.equal(graph.created.length, 2, "필터를 새로 만들면 안 된다.");
  assert.equal(graph.created[0].gain.value, 2);
  assert.equal(graph.created[1].gain.value, 1);
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
  assert.equal(graph.created[0].Q.value, 3);

  context.updateEq(preset([{ frequency: 60, gain: 6 }]));

  assert.equal(
    graph.created[0].Q.value,
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
