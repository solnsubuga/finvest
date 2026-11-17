import { act, render, waitFor } from "@testing-library/react-native";

import { DataSavingProvider } from "@/dataSaving/DataSavingProvider";
import { PREFETCH_DELAY_MS, RoutePrefetch } from "@/navigation/RoutePrefetch";
import { prefetchRoutes, resetPrefetchForTests } from "@/navigation/prefetchRoutes.web";
import { readSettings } from "@/dataSaving/storage";

jest.mock("@/dataSaving/storage", () => ({
  readSettings: jest.fn(),
  writeSettings: jest.fn(async () => {}),
}));

jest.mock("@/navigation/prefetchRoutes", () => ({ prefetchRoutes: jest.fn(async () => {}) }));

type FakeLink = { rel: string; as: string; href: string };

function page(listed: string | null, saveData?: boolean) {
  const added: FakeLink[] = [];
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: {
      querySelector: () => (listed === null ? null : { getAttribute: () => listed }),
      createElement: () => ({ rel: "", as: "", href: "" }),
      head: { appendChild: (link: FakeLink) => added.push(link) },
    },
  });
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { connection: saveData === undefined ? undefined : { saveData } },
  });
  return added;
}

const runtime = globalThis as typeof globalThis & { __loadBundleAsync?: (path: string) => Promise<void> };

/** Expo's bundle loader, recording what it was asked to load. */
function loader(fails: string[] = []) {
  const loaded: string[] = [];
  runtime.__loadBundleAsync = async (path: string) => {
    loaded.push(path);
    if (fails.includes(path)) {
      throw new Error("offline");
    }
  };
  return loaded;
}

const ROUTE = "/_expo/static/js/web/login-0123abcd.js";
const DYNAMIC = "/_expo/static/js/web/[kind]-89ef.js";

describe("loading the web build's screens ahead", () => {
  const realDocument = globalThis.document;
  const realNavigator = globalThis.navigator;

  beforeEach(() => resetPrefetchForTests());
  afterEach(() => {
    delete runtime.__loadBundleAsync;
  });
  afterAll(() => {
    Object.defineProperty(globalThis, "document", { configurable: true, value: realDocument });
    Object.defineProperty(globalThis, "navigator", { configurable: true, value: realNavigator });
  });

  it("loads every listed screen file into the page, once, so no screen opens blank", async () => {
    const added = page(`${ROUTE} ${DYNAMIC}`);
    const loaded = loader();
    await prefetchRoutes();
    await prefetchRoutes();
    expect(loaded).toEqual([ROUTE, DYNAMIC]);
    expect(added).toEqual([]);
  });

  it("goes on to the next file when one cannot be loaded", async () => {
    page(`${ROUTE} ${DYNAMIC}`);
    const loaded = loader([ROUTE]);
    await expect(prefetchRoutes()).resolves.toBeUndefined();
    expect(loaded).toEqual([ROUTE, DYNAMIC]);
  });

  it("loads only this site's own screen files", async () => {
    page(`https://elsewhere.example/x.js /face/loader.mjs /_expo/static/js/web/../../x.js ${ROUTE}`);
    const loaded = loader();
    await prefetchRoutes();
    expect(loaded).toEqual([ROUTE]);
  });

  it("only asks the browser to fetch the files when Expo's loader is missing", async () => {
    const added = page(`${ROUTE} ${DYNAMIC}`);
    await prefetchRoutes();
    expect(added).toEqual([
      { rel: "prefetch", as: "script", href: ROUTE },
      { rel: "prefetch", as: "script", href: DYNAMIC },
    ]);
  });

  it("does nothing when the browser asks to save data", async () => {
    const added = page(ROUTE, true);
    const loaded = loader();
    await prefetchRoutes();
    expect(loaded).toEqual([]);
    expect(added).toEqual([]);
  });

  it("does nothing when the page lists no files", async () => {
    const added = page(null);
    const loaded = loader();
    await prefetchRoutes();
    expect(loaded).toEqual([]);
    expect(added).toEqual([]);
  });
});

describe("when the app loads screens ahead", () => {
  const prefetch = jest.requireMock("@/navigation/prefetchRoutes").prefetchRoutes as jest.Mock;

  beforeEach(() => prefetch.mockClear());

  async function open(light: boolean) {
    (readSettings as jest.Mock).mockResolvedValue({ light, wifiOnlyDocuments: false });
    render(
      <DataSavingProvider>
        <RoutePrefetch />
      </DataSavingProvider>,
    );
    await act(async () => {});
  }

  it("waits until the first screen is up", async () => {
    await open(false);
    expect(prefetch).not.toHaveBeenCalled();
    await waitFor(() => expect(prefetch).toHaveBeenCalledTimes(1), { timeout: PREFETCH_DELAY_MS * 3 });
  });

  it("never loads ahead in light mode", async () => {
    await open(true);
    await act(() => new Promise((resolve) => setTimeout(resolve, PREFETCH_DELAY_MS * 2)));
    expect(prefetch).not.toHaveBeenCalled();
  });
});
