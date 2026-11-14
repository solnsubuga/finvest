import type { Ref } from "react";
import type { StyleProp, ViewStyle } from "react-native";

import type { FaceReading } from "../guidance";
import type { PickedPhoto } from "../photo";

/** What the guide camera can be asked to do from the screen. */
export type GuideCameraHandle = {
  /** Takes a full-size picture now. Null if the camera is not ready. */
  capture(): Promise<PickedPhoto | null>;
};

/**
 * Why the live camera cannot help:
 * - `no_permission`: the client did not allow the camera.
 * - `no_camera`: there is no front camera, or it would not start.
 * - `no_detector`: the camera runs, but faces cannot be found on this device, so the client
 *   takes the picture with the button and the API alone checks it.
 */
export type CameraTrouble = "no_permission" | "no_camera" | "no_detector";

export type GuideCameraProps = {
  /** False pauses the camera, such as while a picture is being sent. */
  active: boolean;
  /** The file name the picture is sent with, such as "pose-left.jpg". */
  fileName: string;
  /** Called for each frame the detector reads, several times a second. */
  onReading: (reading: FaceReading) => void;
  onTrouble: (trouble: CameraTrouble) => void;
  style?: StyleProp<ViewStyle>;
  ref?: Ref<GuideCameraHandle>;
};
