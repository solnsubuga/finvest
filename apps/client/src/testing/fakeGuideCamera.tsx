import { useEffect, useImperativeHandle } from "react";
import { View } from "react-native";

import type { FaceReading } from "@/identity/guidance";
import type { CameraTrouble, GuideCameraProps } from "@/identity/camera/types";

/**
 * Stands in for the live camera in tests, which have no camera or face detector. A test says
 * what the camera sees with `faceCamera.see(...)`, or that it cannot help with `faceCamera.fail(...)`.
 */
type Shown = { onReading: (reading: FaceReading) => void; onTrouble: (trouble: CameraTrouble) => void };

let shown: Shown | null = null;

export const FAKE_FRAME = { uri: "file:///frame.jpg", mimeType: "image/jpeg", size: 2048 };

export const faceCamera = {
  see(reading: FaceReading) {
    shown?.onReading(reading);
  },
  fail(trouble: CameraTrouble) {
    shown?.onTrouble(trouble);
  },
  get open(): boolean {
    return shown !== null;
  },
};

export function GuideCamera({ fileName, onReading, onTrouble, ref }: GuideCameraProps) {
  useEffect(() => {
    shown = { onReading, onTrouble };
    return () => {
      shown = null;
    };
  }, [onReading, onTrouble]);
  useImperativeHandle(ref, () => ({ capture: async () => ({ ...FAKE_FRAME, name: fileName }) }), [fileName]);
  return <View testID="fake-guide-camera" />;
}
