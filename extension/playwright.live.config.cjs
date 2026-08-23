const { defineConfig } = require("@playwright/test");
const base = require("./playwright.config.cjs");

module.exports = defineConfig({
  ...base,
  testMatch: "**/*.live.e2e.cjs",
  testIgnore: [],
});
