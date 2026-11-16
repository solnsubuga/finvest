import { createApiBondsClient } from "./apiBondsClient";
import { BondsClient } from "./types";

type StubModule = typeof import("./stubBondsClient");

/**
 * The in-memory demo invents prices and holdings, so it must never be what a real client
 * reaches. It follows the demo sign-in: loaded in tests, and in a build only when
 * EXPO_PUBLIC_DEMO_AUTH=1 was set on purpose. Written inline, so the bundler leaves the stub out
 * of a normal production build.
 */
function loadStub(): StubModule | null {
  if (process.env.EXPO_PUBLIC_DEMO_AUTH === "1" || process.env.NODE_ENV === "test") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubBondsClient") as StubModule;
  }
  return null;
}

/** Chooses what the bond screens talk to: the demo when it is switched on, otherwise the API. */
export function resolveBondsClient(stub: StubModule | null = loadStub()): BondsClient {
  return stub ? stub.createStubBondsClient({
        failList: demoSearch().includes("bonds=fail"),
        sample: demoSearch().includes("sample=empty") ? "empty" : "default",
      }) : createApiBondsClient();
}

/** On the web, `?bonds=fail` fails the bond list on Invest and the bonds in Portfolio, to show that state. */
function demoSearch(): string {
  return typeof window !== "undefined" ? (window.location?.search ?? "") : "";
}
