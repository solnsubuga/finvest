import { checkPhoto } from "../photo";
import { nextStep, stepAfter, stepNumber, stepsFor } from "../steps";
import { IdentityCheck } from "../types";

function draft(overrides: Partial<IdentityCheck> = {}): IdentityCheck {
  return {
    id: "c",
    status: "draft",
    documentType: "national_id",
    documentCountry: "UG",
    details: null,
    detailsConfirmed: false,
    photos: [],
    liveSelfie: false,
    guidedSelfie: false,
    poses: [],
    missing: ["front", "back", "details", "selfie"],
    retakePhotos: [],
    clientMessage: null,
    submittedAt: null,
    accountNumber: null,
    ...overrides,
  };
}

describe("the steps of the ID check", () => {
  it("follows the canvas: document, front, back, details, selfie; a passport has no back", () => {
    expect(stepsFor("national_id")).toEqual(["document", "front", "back", "details", "selfie"]);
    expect(stepsFor("passport")).toEqual(["document", "front", "details", "selfie"]);
    expect(stepNumber("details", "national_id")).toEqual({ step: 4, of: 5 });
    expect(stepNumber("selfie", "passport")).toEqual({ step: 4, of: 4 });
  });

  it("goes to the first step still to do", () => {
    expect(nextStep(null)).toBe("document");
    expect(nextStep(draft())).toBe("front");
    expect(nextStep(draft({ missing: ["details", "selfie"] }))).toBe("details");
    expect(nextStep(draft({ missing: [] }))).toBeNull();
    expect(nextStep(draft({ status: "needs_review", missing: [] }))).toBeNull();
  });

  it("moves on after a step, and back to anything skipped", () => {
    expect(stepAfter("front", draft({ missing: ["back", "details", "selfie"] }))).toBe("back");
    expect(stepAfter("details", draft({ missing: ["back", "selfie"] }))).toBe("selfie");
    expect(stepAfter("selfie", draft({ missing: ["back"] }))).toBe("back");
    expect(stepAfter("selfie", draft({ missing: [] }))).toBeNull();
  });
});

describe("a photo from the device", () => {
  it("is a JPEG, PNG or WebP of at most 10 MB", () => {
    expect(checkPhoto({ uri: "u", name: "a.jpg", mimeType: "image/jpeg", size: 5 })).toMatchObject({ mimeType: "image/jpeg" });
    expect(checkPhoto({ uri: "u", name: "a.webp", mimeType: null, size: 5 })).toMatchObject({ mimeType: "image/webp" });
    expect(checkPhoto({ uri: "u", name: "a.heic", mimeType: "image/heic", size: 5 })).toBe("wrong_type");
    expect(checkPhoto({ uri: "u", name: "a.png", mimeType: "image/png", size: 10 * 1024 * 1024 + 1 })).toBe("too_large");
    expect(checkPhoto({ uri: "u", name: "a.png", mimeType: "image/png", size: null })).toBe("empty");
  });

  it("puts the head turns after the selfie when the selfie is the guided one", () => {
    expect(stepsFor("national_id", true)).toEqual(["document", "front", "back", "details", "selfie", "poses"]);
    expect(stepNumber("selfie", "passport", true)).toEqual({ step: 4, of: 5 });
    expect(stepNumber("poses", "passport", true)).toEqual({ step: 5, of: 5 });
    const guided = (missing: string[]) => draft({ guidedSelfie: true, missing });
    expect(nextStep(guided(["selfie", "pose_left", "pose_right", "pose_up", "pose_down"]))).toBe("selfie");
    expect(nextStep(guided(["pose_up"]))).toBe("poses");
    expect(stepAfter("selfie", guided(["pose_left", "pose_right", "pose_up", "pose_down"]))).toBe("poses");
    expect(stepAfter("selfie", guided([]))).toBeNull();
    // Not guided: pose entries are never asked for.
    expect(nextStep(draft({ missing: ["pose_up"] }))).toBeNull();
  });
});
