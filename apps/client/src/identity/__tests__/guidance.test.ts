import { FaceReading, guide, LIMITS, movedAngle, Steadiness } from "../guidance";

/** A face the phone would take: one, big, centred, straight, eyes open, good light. */
function face(overrides: Partial<FaceReading> = {}): FaceReading {
  return {
    faces: 1,
    width: 0.45,
    offsetX: 0,
    offsetY: 0,
    yaw: 0,
    pitch: 0,
    leftEyeOpen: 0.95,
    rightEyeOpen: 0.95,
    light: { mean: 130, glare: 0.01, sharpness: 20 },
    ...overrides,
  };
}

describe("live guidance for the straight-on selfie", () => {
  it("takes a good face", () => {
    expect(guide(null, face())).toBeNull();
  });

  it("asks for one face, close and in the middle, first", () => {
    expect(guide(null, { faces: 0 })).toBe("no_face");
    expect(guide(null, face({ faces: 2 }))).toBe("several_faces");
    expect(guide(null, face({ width: LIMITS.faceWidth - 0.01 }))).toBe("face_too_small");
    expect(guide(null, face({ width: undefined }))).toBe("face_too_small");
    expect(guide(null, face({ width: LIMITS.faceWidth }))).toBeNull();
    expect(guide(null, face({ offsetX: LIMITS.offCentre + 0.01 }))).toBe("not_centred");
    expect(guide(null, face({ offsetY: -(LIMITS.offCentre + 0.01) }))).toBe("not_centred");
    expect(guide(null, face({ offsetX: undefined }))).toBe("not_centred");
    expect(guide(null, face({ offsetX: LIMITS.offCentre, offsetY: -LIMITS.offCentre }))).toBeNull();
    // Too small and off centre: size comes first.
    expect(guide(null, face({ width: 0.1, offsetX: 0.4 }))).toBe("face_too_small");
  });

  it("asks for better light where it can be read", () => {
    expect(guide(null, face({ light: { mean: LIMITS.dark - 1, glare: 0, sharpness: 20 } }))).toBe("too_dark");
    expect(guide(null, face({ light: { mean: LIMITS.bright + 1, glare: 0, sharpness: 20 } }))).toBe("too_bright");
    expect(guide(null, face({ light: { mean: 130, glare: LIMITS.glare + 0.01, sharpness: 20 } }))).toBe("glare");
    expect(guide(null, face({ light: { mean: 130, glare: 0, sharpness: LIMITS.sharpness - 1 } }))).toBe("blurry");
    // On a phone light is not read: the API judges it.
    expect(guide(null, face({ light: undefined }))).toBeNull();
  });

  it("asks to look straight, within the margin under the API's 10 degrees", () => {
    expect(guide(null, face({ yaw: LIMITS.straight }))).toBeNull();
    expect(guide(null, face({ yaw: LIMITS.straight + 1 }))).toBe("look_straight");
    expect(guide(null, face({ pitch: -(LIMITS.straight + 1) }))).toBe("look_straight");
    expect(LIMITS.straight).toBeLessThan(10);
  });

  it("asks for open eyes: either eye closed counts when facing the camera", () => {
    expect(guide(null, face({ leftEyeOpen: 0.1 }))).toBe("eyes_closed");
    expect(guide(null, face({ rightEyeOpen: 0.1 }))).toBe("eyes_closed");
    expect(guide(null, face({ leftEyeOpen: undefined, rightEyeOpen: undefined }))).toBeNull();
  });

  it("lets the API decide when the angles are not known", () => {
    expect(guide(null, face({ yaw: undefined }))).toBeNull();
    expect(guide("left", face({ pitch: undefined }))).toBeNull();
  });
});

describe("live guidance for a head turn", () => {
  it("asks to turn far enough, but not so far the face is lost", () => {
    expect(guide("left", face({ yaw: LIMITS.turn - 1 }))).toBe("turn_more");
    expect(guide("left", face({ yaw: LIMITS.turn }))).toBeNull();
    expect(guide("right", face({ yaw: -LIMITS.turn }))).toBeNull();
    expect(guide("right", face({ yaw: -(LIMITS.tooFar + 1) }))).toBe("too_far");
    expect(LIMITS.turn).toBeGreaterThan(20);
  });

  it("asks to nod far enough, by pitch, not yaw", () => {
    expect(guide("up", face({ yaw: 30 }))).toBe("turn_more");
    expect(guide("up", face({ pitch: LIMITS.nod }))).toBeNull();
    expect(guide("down", face({ pitch: -(LIMITS.nod - 1) }))).toBe("turn_more");
    expect(LIMITS.nod).toBeGreaterThan(12);
  });

  it("asks to keep the other angle steady", () => {
    expect(guide("left", face({ yaw: 30, pitch: LIMITS.steady + 1 }))).toBe("hold_steady");
    expect(guide("up", face({ pitch: 20, yaw: -(LIMITS.steady + 1) }))).toBe("hold_steady");
    expect(guide("up", face({ pitch: 20, yaw: LIMITS.steady }))).toBeNull();
    expect(LIMITS.steady).toBeLessThan(15);
  });

  it("asks for the other way when the opposite pose went the same way", () => {
    expect(guide("right", face({ yaw: 30 }), 28)).toBe("wrong_way");
    expect(guide("right", face({ yaw: -30 }), 28)).toBeNull();
    expect(guide("down", face({ pitch: 18 }), 20)).toBe("wrong_way");
    expect(guide("down", face({ pitch: -18 }), 20)).toBeNull();
    // Nothing to compare with.
    expect(guide("right", face({ yaw: 30 }), null)).toBeNull();
  });

  it("counts eyes closed only when both are, while turned away", () => {
    expect(guide("left", face({ yaw: 30, leftEyeOpen: 0.05 }))).toBeNull();
    expect(guide("left", face({ yaw: 30, leftEyeOpen: 0.05, rightEyeOpen: 0.05 }))).toBe("eyes_closed");
    // A nod faces the camera, so one closed eye counts.
    expect(guide("down", face({ pitch: -20, leftEyeOpen: 0.05 }))).toBe("eyes_closed");
  });

  it("keeps the angle a pose moves, to compare its opposite with", () => {
    expect(movedAngle("left", face({ yaw: 31, pitch: 2 }))).toBe(31);
    expect(movedAngle("up", face({ yaw: 3, pitch: -17 }))).toBe(-17);
    expect(movedAngle("up", { faces: 1 })).toBeNull();
  });
});

describe("holding still before the picture is taken", () => {
  it("takes it once the face has been right for the whole hold", () => {
    const steady = new Steadiness(700);
    expect(steady.feed(null, 1000)).toBe(false);
    expect(steady.feed(null, 1500)).toBe(false);
    expect(steady.feed(null, 1700)).toBe(true);
  });

  it("starts again after any problem", () => {
    const steady = new Steadiness(700);
    steady.feed(null, 0);
    expect(steady.feed("turn_more", 600)).toBe(false);
    expect(steady.feed(null, 800)).toBe(false);
    expect(steady.feed(null, 1400)).toBe(false);
    expect(steady.feed(null, 1500)).toBe(true);
    steady.reset();
    expect(steady.feed(null, 1600)).toBe(false);
  });
});
