import { LiveProblem, Pose, turns } from "./poses";

/**
 * Live guidance for the guided selfie: what the phone sees in the camera, and the first thing
 * the client should change. The API judges every frame again with the face quality service
 * (backend/app/modules/kyc/domain/poses.py), so these rules only need to be close. They ask a
 * little more than the API does, because the phone's face detector measures angles and size
 * differently, and a frame that only just passes here could be refused there.
 *
 * Pure: no camera, no clock. `GuideCamera` turns what the detector found into a `FaceReading`.
 */

/** What the detector found in one camera frame. A value it could not measure is left out. */
export type FaceReading = {
  /** How many faces it found. The rest describe the largest one. */
  faces: number;
  /** The face's width, as a share of the picture's width. */
  width?: number;
  /** How far the face's middle is from the picture's middle, as shares of its width and height. */
  offsetX?: number;
  offsetY?: number;
  /** Degrees. Which sign is which way depends on the detector, so only sizes and pairs are compared. */
  yaw?: number;
  pitch?: number;
  /** How likely each eye is open, from 0 to 1. */
  leftEyeOpen?: number;
  rightEyeOpen?: number;
  /**
   * Light over the face, where the detector can read pixels (the web): the mean brightness
   * from 0 to 255, the share of pixels that are almost white, and how sharp the edges are.
   */
  light?: { mean: number; glare: number; sharpness: number };
};

/** The API's limits (domain/poses.py), with the phone's margin on top. */
export const LIMITS = {
  /** The selfie: within this of straight both ways. The API allows 10. */
  straight: 8,
  /** A turn is at least this much yaw. The API asks 20. */
  turn: 25,
  /** A nod is at least this much pitch. The API asks 12. */
  nod: 15,
  /** While turning or nodding, the other angle stays within this. The API allows 15. */
  steady: 12,
  /** Further than this and the far eye and cheek are lost: the phone's own limit. */
  tooFar: 45,
  /** The face's width as a share of the picture's. The API asks a quarter. */
  faceWidth: 0.3,
  /** How far the face's middle may be from the picture's. The API allows a fifth. */
  offCentre: 0.15,
  /** Below this an eye counts as closed. */
  eyeOpen: 0.35,
  /** Mean brightness over the face, 0 to 255. */
  dark: 70,
  bright: 215,
  /** The share of almost white pixels on the face that counts as glare. */
  glare: 0.06,
  /** Edge strength below which the picture is too soft to read. */
  sharpness: 4,
} as const;

/** How long a frame must stay right before it is taken, so a passing moment is not used. */
export const HOLD_MS = 700;

/**
 * The first thing to change, or null when the frame is right. `pose` null is the straight-on
 * selfie. `opposite` is the angle the phone measured for the opposite pose's frame, if one was
 * taken in this visit: the two of a pair must go opposite ways.
 *
 * The order is the order a person fixes things in: get in the picture, get close and centred,
 * then light, then the head's angle, then the eyes, and last hold still.
 */
export function guide(pose: Pose | null, reading: FaceReading, opposite: number | null = null): LiveProblem | null {
  if (reading.faces === 0) return "no_face";
  if (reading.faces > 1) return "several_faces";
  if (reading.width === undefined || reading.width < LIMITS.faceWidth) return "face_too_small";
  if (
    reading.offsetX === undefined ||
    reading.offsetY === undefined ||
    Math.max(Math.abs(reading.offsetX), Math.abs(reading.offsetY)) > LIMITS.offCentre
  ) {
    return "not_centred";
  }
  const light = reading.light;
  if (light && light.mean < LIMITS.dark) return "too_dark";
  if (light && light.mean > LIMITS.bright) return "too_bright";
  if (light && light.glare > LIMITS.glare) return "glare";
  const angle = angleProblem(pose, reading, opposite);
  if (angle) return angle;
  if (eyesClosed(pose, reading)) return "eyes_closed";
  if (light && light.sharpness < LIMITS.sharpness) return "blurry";
  return null;
}

function angleProblem(pose: Pose | null, reading: FaceReading, opposite: number | null): LiveProblem | null {
  const { yaw, pitch } = reading;
  // Without angles the API decides. The phone does not hold the client back.
  if (yaw === undefined || pitch === undefined) return null;
  if (pose === null) {
    return Math.abs(yaw) <= LIMITS.straight && Math.abs(pitch) <= LIMITS.straight ? null : "look_straight";
  }
  const [moved, other] = turns(pose) ? [yaw, pitch] : [pitch, yaw];
  if (Math.abs(moved) < (turns(pose) ? LIMITS.turn : LIMITS.nod)) return "turn_more";
  if (Math.abs(moved) > LIMITS.tooFar) return "too_far";
  if (Math.abs(other) > LIMITS.steady) return "hold_steady";
  if (opposite !== null && opposite !== 0 && opposite > 0 === moved > 0) return "wrong_way";
  return null;
}

function eyesClosed(pose: Pose | null, reading: FaceReading): boolean {
  const closed = [reading.leftEyeOpen, reading.rightEyeOpen].filter(
    (open) => open !== undefined && open < LIMITS.eyeOpen,
  ).length;
  // Turned away, the far eye is often hidden: closed only if both are seen closed, as the API does.
  return pose !== null && turns(pose) ? closed >= 2 : closed > 0;
}

/** The angle a pose moves, as the phone measured it: kept to compare the opposite pose with. */
export function movedAngle(pose: Pose, reading: FaceReading): number | null {
  const value = turns(pose) ? reading.yaw : reading.pitch;
  return value ?? null;
}

/**
 * Follows the readings as they come, and says when the frame has been right for long enough to
 * take. Any problem starts the wait again.
 */
export class Steadiness {
  private since: number | null = null;

  constructor(private readonly holdMs: number = HOLD_MS) {}

  /** `now` in milliseconds. True once there has been no problem for `holdMs`. */
  feed(problem: LiveProblem | null, now: number): boolean {
    if (problem !== null) {
      this.since = null;
      return false;
    }
    if (this.since === null) this.since = now;
    return now - this.since >= this.holdMs;
  }

  reset(): void {
    this.since = null;
  }
}
