import { tokenFromLink } from "../emailLink";

describe("the token in an email link", () => {
  it("is what comes after the #", () => {
    expect(tokenFromLink("https://app.finvestug.com/verify-email#abcDEF_123-xyz7890")).toBe("abcDEF_123-xyz7890");
    expect(tokenFromLink("finvest://verify-email#abcDEF_123-xyz7890")).toBe("abcDEF_123-xyz7890");
  });

  it("is missing when there is no #, or nothing like a token after it", () => {
    expect(tokenFromLink("https://app.finvestug.com/verify-email")).toBeNull();
    expect(tokenFromLink("https://app.finvestug.com/verify-email#")).toBeNull();
    expect(tokenFromLink("https://app.finvestug.com/verify-email#short")).toBeNull();
    expect(tokenFromLink("https://app.finvestug.com/verify-email#<script>alert(1)</script>")).toBeNull();
    expect(tokenFromLink(null)).toBeNull();
  });
});
