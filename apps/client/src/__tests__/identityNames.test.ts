import { firstGivenName, properCase } from "@/identity/names";

describe("properCase", () => {
  it.each([
    ["AMARA", "Amara"],
    ["amara", "Amara"],
    ["  AMARA   JOY ", "Amara Joy"],
    ["SSALI-KATO", "Ssali-Kato"],
    ["O'NEIL", "O'Neil"],
    ["McDonald", "McDonald"],
    ["", ""],
  ])("shows %p as %p", (printed, shown) => {
    expect(properCase(printed)).toBe(shown);
  });
});

describe("firstGivenName", () => {
  it("is the first given name in proper case", () => {
    expect(firstGivenName("AMARA JOY")).toBe("Amara");
  });
  it("is null when there is none", () => {
    expect(firstGivenName("  ")).toBeNull();
  });
});
