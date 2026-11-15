import { describe, expect, it } from "vitest";
import { careersLink, initials, storeLinks } from "./company-links.ts";

describe("store links", () => {
  it("shows none until a listing is entered", () => {
    expect(storeLinks({ appStoreUrl: null, playStoreUrl: null })).toEqual([]);
    expect(storeLinks({ appStoreUrl: "  ", playStoreUrl: "" })).toEqual([]);
  });

  it("shows each store's own https listing", () => {
    expect(
      storeLinks({
        appStoreUrl: "https://apps.apple.com/ug/app/finvest/id123",
        playStoreUrl: "https://play.google.com/store/apps/details?id=com.finvestug.app",
      }),
    ).toEqual([
      { label: "App Store", href: "https://apps.apple.com/ug/app/finvest/id123" },
      { label: "Google Play", href: "https://play.google.com/store/apps/details?id=com.finvestug.app" },
    ]);
  });

  it("refuses a link to anywhere else, over http, or to the store's home page", () => {
    expect(
      storeLinks({ appStoreUrl: "http://apps.apple.com/ug/app/finvest/id123", playStoreUrl: "https://example.com/app" }),
    ).toEqual([]);
    expect(storeLinks({ appStoreUrl: "https://apps.apple.com/", playStoreUrl: "not a link" })).toEqual([]);
  });
});

describe("careers link", () => {
  it("is a mailto for a Finvest address only", () => {
    expect(careersLink(null)).toBeNull();
    expect(careersLink("careers@finvestug.com")).toBe("mailto:careers@finvestug.com");
    expect(careersLink(" Careers@FinvestUG.com ")).toBe("mailto:careers@finvestug.com");
    expect(careersLink("jobs@example.com")).toBeNull();
  });
});

describe("initials", () => {
  it("takes the first and last name", () => {
    expect(initials("Amara Nakato")).toBe("AN");
    expect(initials("Amara Grace Nakato")).toBe("AN");
    expect(initials("amara")).toBe("A");
    expect(initials("  ")).toBe("");
  });
});
