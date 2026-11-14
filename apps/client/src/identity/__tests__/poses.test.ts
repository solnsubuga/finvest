import { FRAME_PROBLEMS, isFrameProblem, liveCopy, posesMissing, retakeCopy } from "../poses";

describe("the guided selfie's poses", () => {
  it("lists the poses still to take, only for a guided selfie", () => {
    expect(posesMissing({ guidedSelfie: true, missing: ["pose_up", "selfie", "pose_left"] })).toEqual(["left", "up"]);
    expect(posesMissing({ guidedSelfie: false, missing: ["pose_up"] })).toEqual([]);
  });

  it("knows the API's refusal codes and nothing else", () => {
    expect(isFrameProblem("turn_more")).toBe(true);
    expect(isFrameProblem("selfie_first")).toBe(true);
    expect(isFrameProblem("not_an_image_of_that_type")).toBe(false);
  });

  it("has a retake prompt for every refusal, for the selfie and each pose", () => {
    for (const problem of FRAME_PROBLEMS) {
      for (const pose of [null, "left", "right", "up", "down"] as const) {
        expect(retakeCopy(problem, pose).length).toBeGreaterThan(10);
      }
    }
    expect(retakeCopy("turn_more", "left")).toBe("Turn your head further to your left.");
    expect(retakeCopy("turn_more", "down")).toBe("Tilt your chin further down.");
    expect(retakeCopy("wrong_way", "right")).toMatch(/Turn to your right this time/);
  });

  it("says what to change while the camera runs", () => {
    expect(liveCopy(null, "up")).toBe("Hold it there");
    expect(liveCopy("turn_more", "right")).toBe("Keep turning to your right");
    expect(liveCopy("turn_more", "up")).toBe("Keep looking up");
    expect(liveCopy("face_too_small", null)).toBe("Move a little closer");
  });
});
