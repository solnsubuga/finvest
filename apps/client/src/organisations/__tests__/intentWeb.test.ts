import { clearIntent, readIntent, writeIntent } from "@/organisations/intent.web";
import { intentFor } from "@/organisations/intentTypes";

jest.mock("expo-crypto", () => ({
  CryptoDigestAlgorithm: { SHA256: "SHA-256" },
  digestStringAsync: async (_algorithm: string, text: string) =>
    jest.requireActual("node:crypto").createHash("sha256").update(text).digest("hex"),
}));

const saved = new Map<string, string>();

beforeEach(() => {
  saved.clear();
  Object.defineProperty(globalThis, "window", {
    value: {
      localStorage: {
        getItem: (key: string) => saved.get(key) ?? null,
        setItem: (key: string, value: string) => saved.set(key, value),
        removeItem: (key: string) => saved.delete(key),
      },
    },
    configurable: true,
  });
});

describe("the company choice in a browser", () => {
  it("keeps a digest of the number, never the number", async () => {
    await writeIntent("+256772900413");
    const stored = [...saved.values()].join("");
    expect(stored).not.toContain("772900413");
    expect(stored).toMatch(/"digest":"[0-9a-f]{64}"/);
    expect(await intentFor(await readIntent(), "+256772900413")).toBe(true);
    expect(await intentFor(await readIntent(), "+256772123456")).toBe(false);
  });

  it("is gone once cleared", async () => {
    await writeIntent("+256772900413");
    await clearIntent();
    expect(saved.size).toBe(0);
    expect(await readIntent()).toBeNull();
  });
});
