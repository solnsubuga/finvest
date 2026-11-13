import { describe, expect, it } from "vitest";
import { creationOptions, fromBase64Url, requestOptions, toBase64Url } from "./webauthn.ts";

describe("passkey options", () => {
  it("round-trips base64url without padding", () => {
    for (const text of ["", "f", "fo", "foo", "foob", "ÿþ"]) {
      const bytes = Uint8Array.from(text, (character) => character.charCodeAt(0));
      const encoded = toBase64Url(bytes.buffer);
      expect(encoded).not.toMatch(/[+/=]/);
      expect(new Uint8Array(fromBase64Url(encoded))).toEqual(bytes);
    }
  });

  it("decodes the challenge, the user ID and the credential IDs", () => {
    const created = creationOptions({
      challenge: "AQID",
      user: { id: "BAUG", name: "grace" },
      excludeCredentials: [{ id: "BwgJ", type: "public-key" }],
      rp: { id: "finvestug.com", name: "Finvest" },
    });
    expect(new Uint8Array(created.challenge as ArrayBuffer)).toEqual(new Uint8Array([1, 2, 3]));
    expect(new Uint8Array(created.user.id as ArrayBuffer)).toEqual(new Uint8Array([4, 5, 6]));
    expect(new Uint8Array(created.excludeCredentials![0]!.id as ArrayBuffer)).toEqual(new Uint8Array([7, 8, 9]));
    const requested = requestOptions({ challenge: "AQID", allowCredentials: [] });
    expect(requested.allowCredentials).toEqual([]);
  });
});
