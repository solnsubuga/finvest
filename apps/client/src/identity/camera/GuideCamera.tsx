import { useCallback, useEffect, useImperativeHandle, useRef } from "react";
import { LayoutChangeEvent, StyleSheet, View } from "react-native";
import { File } from "expo-file-system";
import { useCameraPermission, usePhotoOutput } from "react-native-vision-camera";
import { Camera as FaceCamera, type Face } from "react-native-vision-camera-face-detector";

import type { FaceReading } from "../guidance";
import type { GuideCameraProps } from "./types";

/**
 * The live front camera on a phone, with Google's on-device face detector (ML Kit, through
 * VisionCamera). Faces are found on the phone and never sent anywhere; only the picture the
 * client takes is uploaded. The detector gives the face's place, size, angles and how open each
 * eye is. It cannot read light, so on a phone the API alone judges dark, bright, glare and blur.
 */
export function GuideCamera({ active, fileName, onReading, onTrouble, style, ref }: GuideCameraProps) {
  const { hasPermission, requestPermission } = useCameraPermission();
  const photoOutput = usePhotoOutput({ containerFormat: "jpeg", quality: 0.9 });
  const size = useRef({ width: 0, height: 0 });
  const asked = useRef(false);

  useEffect(() => {
    if (hasPermission || asked.current) return;
    asked.current = true;
    void requestPermission().then((granted) => {
      if (!granted) onTrouble("no_permission");
    });
  }, [hasPermission, requestPermission, onTrouble]);

  useImperativeHandle(
    ref,
    () => ({
      async capture() {
        const photo = await photoOutput.capturePhotoToFile({ enableShutterSound: false }, {});
        const uri = photo.filePath.startsWith("file://") ? photo.filePath : `file://${photo.filePath}`;
        return { uri, name: fileName, mimeType: "image/jpeg", size: new File(uri).size };
      },
    }),
    [photoOutput, fileName],
  );

  const onLayout = useCallback((event: LayoutChangeEvent) => {
    size.current = { width: event.nativeEvent.layout.width, height: event.nativeEvent.layout.height };
  }, []);

  const onFacesDetected = useCallback(
    (faces: Face[]) => {
      const { width, height } = size.current;
      if (width === 0 || height === 0) return;
      onReading(toReading(faces, width, height));
    },
    [onReading],
  );

  const onError = useCallback(() => onTrouble("no_detector"), [onTrouble]);

  if (!hasPermission) return <View style={[styles.fill, style]} />;
  return (
    <View style={[styles.fill, style]} onLayout={onLayout}>
      <FaceCamera
        style={StyleSheet.absoluteFill}
        device="front"
        isActive={active}
        outputs={[photoOutput]}
        resizeMode="cover"
        // Faces in the preview's own points, so they can be compared with the oval on screen.
        autoMode
        performanceMode="fast"
        runClassifications
        minFaceSize={0.15}
        onFacesDetected={onFacesDetected}
        onError={onError}
      />
    </View>
  );
}

/** The largest face, in shares of the preview, as the guidance rules read it. */
export function toReading(faces: Face[], width: number, height: number): FaceReading {
  if (faces.length === 0) return { faces: 0 };
  const face = faces.reduce((big, next) => (next.bounds.width > big.bounds.width ? next : big));
  const { x, y, width: w, height: h } = face.bounds;
  return {
    faces: faces.length,
    width: w / width,
    offsetX: (x + w / 2) / width - 0.5,
    offsetY: (y + h / 2) / height - 0.5,
    yaw: face.yawAngle,
    pitch: face.pitchAngle,
    leftEyeOpen: face.leftEyeOpenProbability,
    rightEyeOpen: face.rightEyeOpenProbability,
  };
}

const styles = StyleSheet.create({
  fill: { flex: 1, backgroundColor: "#000" },
});
