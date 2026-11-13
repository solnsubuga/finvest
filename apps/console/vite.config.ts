/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  // The sample sign-in lets anyone in. It must never reach the real console.
  const env = {
    ...loadEnv(mode, import.meta.dirname, "VITE_"),
    ...process.env,
  };
  if (env.VITE_STUB_API === "true" && env.VITE_DEPLOY_ENV === "production") {
    throw new Error(
      "VITE_STUB_API=true cannot be combined with VITE_DEPLOY_ENV=production: the sample sign-in accepts any password.",
    );
  }

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    server: { port: 5174 },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      css: false,
    },
  };
});
