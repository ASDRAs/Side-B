const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "..", "scripts", "eqView.js"), "utf8");
const modulePromise = import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

test("stable genre IDs are rendered as product-facing labels", async () => {
  const { eqStatusText, genreDisplayName } = await modulePromise;
  assert.equal(genreDisplayName("dance"), "댄스");
  assert.equal(genreDisplayName("rnb_soul"), "R&B/Soul");
  assert.equal(eqStatusText({ status: "applied", mode: "auto", genre: "rock_metal" }), "록/메탈 EQ 적용 중");
});

test("an unknown future genre remains readable", async () => {
  const { genreDisplayName } = await modulePromise;
  assert.equal(genreDisplayName("future_genre"), "future_genre");
  assert.equal(genreDisplayName("toString"), "toString");
});
