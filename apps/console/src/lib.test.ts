import { describe, expect, it } from "vitest";

import { initials, shownName } from "./lib.ts";

describe("shownName", () => {
  it("puts given names first in proper case", () => {
    expect(shownName("NAKATO", "AMARA")).toBe("Amara Nakato");
    expect(shownName("NAKATO", "Amara Joy")).toBe("Amara Joy Nakato");
    expect(shownName("  ssali-kato ", "o'neil")).toBe("O'Neil Ssali-Kato");
  });

  it("keeps a name that already mixes capitals", () => {
    expect(shownName("McDonald", "PETER")).toBe("Peter McDonald");
  });

  it("gives initials in the same order", () => {
    expect(initials(shownName("NAKATO", "AMARA"))).toBe("AN");
  });
});
