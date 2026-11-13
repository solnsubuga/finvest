import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StubBanner } from "@/components/StubBanner.tsx";
import { AuthError } from "./api.ts";
import { apiBaseUrl } from "@/api/http.ts";
import { createAuthApi, isStubAuth } from "./index.ts";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("choosing the sign-in implementation", () => {
  it("leaves the sample sign-in out of a normal production build", async () => {
    vi.stubEnv("DEV", false);
    vi.stubEnv("VITE_STUB_API", "");

    expect(isStubAuth()).toBe(false);
    await expect(createAuthApi().startSignIn("admin@finvest.test", "anything")).rejects.toEqual(
      new AuthError("not_connected"),
    );
  });

  it("uses the sample sign-in in development and when a build asks for it", () => {
    vi.stubEnv("DEV", true);
    vi.stubEnv("VITE_API_URL", "");
    expect(isStubAuth()).toBe(true);

    vi.stubEnv("DEV", false);
    vi.stubEnv("VITE_STUB_API", "true");
    expect(isStubAuth()).toBe(true);
  });

  it("uses the real API in development when VITE_API_URL points at one", () => {
    vi.stubEnv("DEV", true);
    vi.stubEnv("VITE_STUB_API", "");
    vi.stubEnv("VITE_API_URL", "http://127.0.0.1:8000");
    expect(isStubAuth()).toBe(false);
  });

  it("finds the deployed API from the deploy's name", () => {
    vi.stubEnv("VITE_API_URL", "");
    vi.stubEnv("VITE_DEPLOY_ENV", "production");
    expect(apiBaseUrl()).toBe("https://api.finvestug.com");
    vi.stubEnv("VITE_DEPLOY_ENV", "");
    expect(apiBaseUrl()).toBeNull();
  });

  it("has a banner that says the sign-in is a sample", () => {
    render(<StubBanner />);
    expect(screen.getByRole("note")).toHaveTextContent("Sample sign-in. Not connected to Finvest.");
  });
});
