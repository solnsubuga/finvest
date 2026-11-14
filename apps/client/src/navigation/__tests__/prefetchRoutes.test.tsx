import { act, render, waitFor } from "@testing-library/react-native";

import { DataSavingProvider } from "@/dataSaving/DataSavingProvider";
import { PREFETCH_DELAY_MS, RoutePrefetch } from "@/navigation/RoutePrefetch";
import { prefetchRoutes, resetPrefetchForTests } from "@/navigation/prefetchRoutes.web";
import { readSettings } from "@/dataSaving/storage";

jest.mock("@/dataSaving/storage", () => ({
  readSettings: jest.fn(),
  writeSettings: jest.fn(async () => {}),
}));

jest.mock("@/navigation/prefetchRoutes", () => ({ prefetchRoutes: jest.fn() }));

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

const ROUTE = "/_expo/static/js/web/login-0123abcd.js";
const DYNAMIC = "/_expo/static/js/web/[kind]-89ef.js";

describe("fetching the web build's screens ahead", () => {
  const realDocument = globalThis.document;
  const realNavigator = globalThis.navigator;

  beforeEach(() => resetPrefetchForTests());
  afterAll(() => {
    Object.defineProperty(globalThis, "document", { configurable: true, value: realDocument });
    Object.defineProperty(globalThis, "navigator", { configurable: true, value: realNavigator });
  });

  it("asks the browser to prefetch every listed screen file, once", () => {
    const added = page(`${ROUTE} ${DYNAMIC}`);
    prefetchRoutes();
    prefetchRoutes();
    expect(added).toEqual([
      { rel: "prefetch", as: "script", href: ROUTE },
      { rel: "prefetch", as: "script", href: DYNAMIC },
    ]);
  });

  it("fetches only this site's own screen files", () => {
    const added = page(`https://elsewhere.example/x.js /face/loader.mjs /_expo/static/js/web/../../x.js ${ROUTE}`);
    prefetchRoutes();
    expect(added.map((link) => link.href)).toEqual([ROUTE]);
  });

  it("does nothing when the browser asks to save data", () => {
    const added = page(ROUTE, true);
    prefetchRoutes();
    expect(added).toEqual([]);
  });

  it("does nothing when the page lists no files", () => {
    const added = page(null);
    prefetchRoutes();
    expect(added).toEqual([]);
  });
});

describe("when the app fetches screens ahead", () => {
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

  it("never fetches ahead in light mode", async () => {
    await open(true);
    await act(() => new Promise((resolve) => setTimeout(resolve, PREFETCH_DELAY_MS * 2)));
    expect(prefetch).not.toHaveBeenCalled();
  });
});
