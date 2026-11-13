import { defineConfig } from "vitest/config";

// Checks on the built site in dist/. Run `pnpm build` first.
export default defineConfig({
  test: { include: ["tests/**/*.test.ts"] },
});
