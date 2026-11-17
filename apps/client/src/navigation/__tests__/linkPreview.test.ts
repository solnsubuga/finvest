import { readFileSync } from "node:fs";
import { join } from "node:path";

// The preview drawn when an app address is shared (public/index.html). Apps that draw it read the
// page's tags without running any script.
const publicDir = join(__dirname, "..", "..", "..", "public");
const html = readFileSync(join(publicDir, "index.html"), "utf8");
const meta = (key: string) =>
  html.match(new RegExp(`<meta\\s+(?:property|name)="${key}"\\s+content="([^"]*)"`))?.[1];

describe("the link preview", () => {
  it("is in the page itself, with a title, a description and a large picture", () => {
    for (const key of ["og:title", "og:description", "og:url", "og:image:alt", "twitter:title", "twitter:description"]) {
      expect(meta(key)).toBeTruthy();
    }
    expect(meta("twitter:card")).toBe("summary_large_image");
    expect(meta("og:image")).toBe("https://app.finvestug.com/share.png");
    expect(meta("twitter:image")).toBe(meta("og:image"));
  });

  it("uses the website's home picture, 1200 by 630 and small enough for WhatsApp", () => {
    const png = readFileSync(join(publicDir, "share.png"));
    expect(png.subarray(1, 4).toString("ascii")).toBe("PNG");
    expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([1200, 630]);
    expect(png.length).toBeLessThan(300 * 1024);
    const site = readFileSync(join(publicDir, "..", "..", "site", "public", "share", "home.png"));
    expect(png.equals(site)).toBe(true);
  });
});
