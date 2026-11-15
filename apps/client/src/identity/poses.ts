import type { IdentityCheck } from "./types";

/**
 * The guided selfie (docs/identity-checks.md#guided-selfie): after the straight-on selfie the
 * client turns left, turns right, looks up and looks down, and a frame is kept for each. The
 * API checks every frame again before keeping it (backend/app/modules/kyc/domain/poses.py).
 */

export type Pose = "left" | "right" | "up" | "down";

/** The order the app asks for them, as the API lists them. */
export const POSES: Pose[] = ["left", "right", "up", "down"];

export const OPPOSITE: Record<Pose, Pose> = { left: "right", right: "left", up: "down", down: "up" };

/** A turn moves the yaw, a nod the pitch. */
export function turns(pose: Pose): boolean {
  return pose === "left" || pose === "right";
}

export const poseCopy: Record<Pose, { title: string; short: string; how: string; done: string }> = {
  left: {
    title: "Turn your head to your left",
    short: "Turn left",
    how: "Keep your phone still. Turn only your head, slowly, until we take the picture.",
    done: "Turned left",
  },
  right: {
    title: "Turn your head to your right",
    short: "Turn right",
    how: "Keep your phone still. Turn only your head, slowly, until we take the picture.",
    done: "Turned right",
  },
  up: {
    title: "Look up a little",
    short: "Look up",
    how: "Keep your phone still. Tilt your chin up slowly, until we take the picture.",
    done: "Looked up",
  },
  down: {
    title: "Look down a little",
    short: "Look down",
    how: "Keep your phone still. Tilt your chin down slowly, until we take the picture.",
    done: "Looked down",
  },
};

/**
 * Why the API refused a frame or the guided selfie. Each is a prompt the app shows before the
 * client takes it again. Mirrors the backend's `Quality`, plus `selfie_first`.
 */
export type FrameProblem =
  | "no_face"
  | "several_faces"
  | "face_too_small"
  | "not_centred"
  | "blurry"
  | "too_dark"
  | "too_bright"
  | "glare"
  | "eyes_closed"
  | "look_straight"
  | "turn_more"
  | "hold_steady"
  | "wrong_way"
  | "not_read"
  | "selfie_first";

export const FRAME_PROBLEMS: readonly FrameProblem[] = [
  "no_face",
  "several_faces",
  "face_too_small",
  "not_centred",
  "blurry",
  "too_dark",
  "too_bright",
  "glare",
  "eyes_closed",
  "look_straight",
  "turn_more",
  "hold_steady",
  "wrong_way",
  "not_read",
  "selfie_first",
];

export function isFrameProblem(code: string): code is FrameProblem {
  return (FRAME_PROBLEMS as readonly string[]).includes(code);
}

/** A problem the phone can see while the camera runs. `too_far` is the phone's own. */
export type LiveProblem = Exclude<FrameProblem, "not_read" | "selfie_first"> | "too_far";

function side(pose: Pose): string {
  return pose === "left" || pose === "right" ? `to your ${pose}` : pose;
}

/** The sentence shown after the API refused a frame: what to do differently. `pose` null is the selfie. */
export function retakeCopy(problem: FrameProblem, pose: Pose | null): string {
  switch (problem) {
    case "no_face":
      return "We could not see your face. Fit it inside the oval and try again.";
    case "several_faces":
      return "We could see more than one face. Make sure only you are in the picture.";
    case "face_too_small":
      return "Your face was too far away. Bring the phone closer, so your face fills the oval.";
    case "not_centred":
      return "Your face was not in the middle. Keep it inside the oval.";
    case "blurry":
      return "The picture was blurry. Hold the phone still, and wipe the camera if it is smudged.";
    case "too_dark":
      return "The picture was too dark. Face a window or a light, then try again.";
    case "too_bright":
      return "The picture was too bright. Move out of direct sunlight.";
    case "glare":
      return "There was glare on your face or glasses. Move away from the light, or take your glasses off.";
    case "eyes_closed":
      return "Your eyes looked closed. Keep them open.";
    case "look_straight":
      return "Look straight at the camera, with your head level.";
    case "turn_more":
      if (!pose) return "Look straight at the camera, with your head level.";
      return turns(pose) ? `Turn your head further ${side(pose)}.` : `Tilt your chin further ${pose}.`;
    case "hold_steady":
      if (!pose) return "Look straight at the camera, with your head level.";
      return turns(pose) ? "Keep your chin level while you turn." : "Keep facing forward while you tilt your chin.";
    case "wrong_way":
      if (!pose) return "Look straight at the camera, with your head level.";
      return turns(pose)
        ? `That was the same way as your other turn. Turn ${side(pose)} this time.`
        : `That was the same way as your other tilt. Look ${pose} this time.`;
    case "selfie_first":
      return "Take your selfie first, then the head turns.";
    case "not_read":
      return "We could not read that picture. Please take it again.";
  }
}

/** The short prompt under the camera while it runs. `pose` null is the selfie. */
export function liveCopy(problem: LiveProblem | null, pose: Pose | null): string {
  switch (problem) {
    case null:
      return "Hold it there";
    case "no_face":
      return "Fit your face in the oval";
    case "several_faces":
      return "Only you in the picture";
    case "face_too_small":
      return "Move a little closer";
    case "not_centred":
      return "Move your face to the middle";
    case "blurry":
      return "Hold the phone still";
    case "too_dark":
      return "Find more light";
    case "too_bright":
      return "Too bright. Move out of the sun";
    case "glare":
      return "Glare on your face. Move from the light";
    case "eyes_closed":
      return "Keep your eyes open";
    case "look_straight":
      return "Look straight at the camera";
    case "turn_more":
      if (!pose) return "Look straight at the camera";
      return turns(pose) ? `Keep turning ${side(pose)}` : `Keep looking ${pose}`;
    case "too_far":
      return pose && turns(pose) ? "Not so far. Turn back a little" : "Not so far. Come back a little";
    case "hold_steady":
      return pose && turns(pose) ? "Keep your chin level" : "Keep facing forward";
    case "wrong_way":
      if (!pose) return "Look straight at the camera";
      return turns(pose) ? `Other way: turn ${side(pose)}` : `Other way: look ${pose}`;
  }
}

/**
 * Whether the check takes the head turns: the guided selfie, unless its selfie came from a
 * passed live check, which is the proof on its own (the owner's choice, 2026-11-15). The API
 * then lists no poses as missing.
 */
export function headTurns(check: Pick<IdentityCheck, "guidedSelfie" | "liveSelfie">): boolean {
  return check.guidedSelfie && !check.liveSelfie;
}

/**
 * Whether the progress bar counts the head turns. Before the selfie, a build that runs Regula's
 * liveness expects none, since a passed check needs none; a camera selfie brings them back.
 */
export function turnsCounted(check: Pick<IdentityCheck, "guidedSelfie" | "liveSelfie" | "photos">, regula: boolean): boolean {
  return headTurns(check) && (check.photos.includes("selfie") || !regula);
}

/** The poses the check still needs, in order. Empty when the selfie is not the guided one. */
export function posesMissing(check: Pick<IdentityCheck, "guidedSelfie" | "missing">): Pose[] {
  if (!check.guidedSelfie) return [];
  return POSES.filter((pose) => check.missing.includes(`pose_${pose}`));
}
