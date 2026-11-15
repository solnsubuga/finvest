import { describe, expect, it } from "vitest";
import { androidAssetLinks, appleAssociation } from "./app-association.ts";

const CERT = Array.from({ length: 32 }, (_, i) => i.toString(16).padStart(2, "0")).join(":");

describe("the files that let the phone apps use finvestug.com passkeys", () => {
  it("name the iPhone app by its team and id", () => {
    expect(appleAssociation("ABCDE12345")).toEqual({ webcredentials: { apps: ["ABCDE12345.com.finvestug.app"] } });
  });

  it("name the Android app by its id and signing certificates", () => {
    const [link] = androidAssetLinks(`${CERT}, ${CERT.toUpperCase()}`) as { target: Record<string, unknown>; relation: string[] }[];
    expect(link?.relation).toContain("delegate_permission/common.get_login_creds");
    expect(link?.target).toEqual({
      namespace: "android_app",
      package_name: "com.finvestug.app",
      sha256_cert_fingerprints: [CERT.toUpperCase(), CERT.toUpperCase()],
    });
  });

  it("vouch for nothing until the build is given the app's details", () => {
    expect(appleAssociation(undefined)).toEqual({ webcredentials: { apps: [] } });
    expect(androidAssetLinks("")).toEqual([]);
  });

  it("stop the build on a value that is not a team ID or a fingerprint", () => {
    expect(() => appleAssociation("TEAM")).toThrow(/team ID/);
    expect(() => androidAssetLinks("AB:CD")).toThrow(/fingerprints/);
  });
});
