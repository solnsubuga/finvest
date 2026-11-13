import { defineConfig } from "astro/config";

// The address the site is served from. Staging passes its own.
const site = process.env.PUBLIC_SITE_URL ?? "https://finvestug.com";

export default defineConfig({
  site,
  output: "static",
  trailingSlash: "ignore",
  build: { format: "directory" },
  devToolbar: { enabled: false },
});
