import { defineConfig, devices } from "@playwright/test";

// The client app and the console, built for the web and served as the hosting serves them.
// Both run against their in-app sample APIs until the generated API client is wired in.
const CLIENT = "http://127.0.0.1:4310";
const CONSOLE = "http://127.0.0.1:4320";
const PHONE = { ...devices["Pixel 7"], defaultBrowserType: "chromium" as const };
const WIDE = { viewport: { width: 1280, height: 860 } };

export default defineConfig({
  testDir: "tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  // A pass on retry is not a pass.
  retries: 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    trace: "off",
    screenshot: "only-on-failure",
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH }
      : {},
  },
  projects: [
    { name: "client-phone", testMatch: /client\.spec\.ts/, use: { ...PHONE, baseURL: CLIENT } },
    { name: "client-wide", testMatch: /client\.spec\.ts/, use: { ...WIDE, baseURL: CLIENT } },
    { name: "console-wide", testMatch: /console\.spec\.ts/, use: { ...WIDE, baseURL: CONSOLE } },
    { name: "console-phone", testMatch: /console\.spec\.ts/, use: { ...PHONE, baseURL: CONSOLE } },
  ],
  webServer: [
    { command: "node serve.mjs .builds/client 4310", url: CLIENT, reuseExistingServer: false },
    { command: "node serve.mjs .builds/console 4320", url: CONSOLE, reuseExistingServer: false },
  ],
});
