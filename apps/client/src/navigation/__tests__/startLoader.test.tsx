import { readFileSync } from "node:fs";
import { join } from "node:path";

import { renderHook, waitFor } from "@testing-library/react-native";

import { hideStartLoader, START_LOADER_FADE_MS, START_LOADER_ID } from "@/navigation/startLoader.web";
import { useStartLoaderDone } from "@/navigation/useStartLoaderDone";

jest.mock("@/navigation/startLoader", () => ({ hideStartLoader: jest.fn() }));

const mockFonts = { ready: true };
jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [mockFonts.ready, null],
}));

type FakeLoader = {
  attributes: Map<string, string>;
  removed: boolean;
  hasAttribute: (name: string) => boolean;
  setAttribute: (name: string, value: string) => void;
  remove: () => void;
};

function fakeLoader(): FakeLoader {
  const loader: FakeLoader = {
    attributes: new Map(),
    removed: false,
    hasAttribute: (name) => loader.attributes.has(name),
    setAttribute: (name, value) => void loader.attributes.set(name, value),
    remove: () => void (loader.removed = true),
  };
  return loader;
}

describe("the web page's start loader", () => {
  const realDocument = globalThis.document;
  afterEach(() => {
    jest.useRealTimers();
    Object.defineProperty(globalThis, "document", { configurable: true, value: realDocument });
  });

  function page(loader: FakeLoader | null) {
    const asked: string[] = [];
    Object.defineProperty(globalThis, "document", {
      configurable: true,
      value: {
        getElementById: (id: string) => {
          asked.push(id);
          return loader;
        },
      },
    });
    return asked;
  }

  it("fades out, then leaves the page", () => {
    jest.useFakeTimers();
    const loader = fakeLoader();
    const asked = page(loader);
    hideStartLoader();
    expect(asked).toEqual([START_LOADER_ID]);
    expect(loader.attributes.has("data-leaving")).toBe(true);
    expect(loader.attributes.get("aria-busy")).toBe("false");
    expect(loader.removed).toBe(false);
    jest.advanceTimersByTime(START_LOADER_FADE_MS);
    expect(loader.removed).toBe(true);
  });

  it("is removed once, however many screens ask", () => {
    jest.useFakeTimers();
    const loader = fakeLoader();
    page(loader);
    const remove = jest.spyOn(loader, "remove");
    hideStartLoader();
    hideStartLoader();
    jest.advanceTimersByTime(START_LOADER_FADE_MS);
    expect(remove).toHaveBeenCalledTimes(1);
  });

  it("does nothing when the page has no loader", () => {
    page(null);
    expect(() => hideStartLoader()).not.toThrow();
  });

  it("is in the page itself, with no inline script for the CSP to allow", () => {
    const html = readFileSync(join(__dirname, "..", "..", "..", "public", "index.html"), "utf8");
    expect(html).toContain(`<div id="${START_LOADER_ID}"`);
    expect(html).toContain('<div id="root"></div>');
    expect(html).not.toMatch(/<script\b/i);
  });
});

describe("when the first screen is up", () => {
  const hide = jest.requireMock("@/navigation/startLoader").hideStartLoader as jest.Mock;
  beforeEach(() => {
    hide.mockClear();
    mockFonts.ready = true;
  });

  it("removes the loader once the screen has been painted", async () => {
    renderHook(() => useStartLoaderDone());
    await waitFor(() => expect(hide).toHaveBeenCalled());
  });

  it("keeps it until the app's font is in", async () => {
    mockFonts.ready = false;
    renderHook(() => useStartLoaderDone());
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(hide).not.toHaveBeenCalled();
  });
});
