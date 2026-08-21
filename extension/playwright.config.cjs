const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.cjs",
  outputDir: "./test-results",
  timeout: 120_000,
  expect: {
    timeout: 100_000,
  },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    headless: true,
  },
});
