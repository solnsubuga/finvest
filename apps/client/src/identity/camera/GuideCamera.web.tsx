import { useCallback, useEffect, useImperativeHandle, useRef } from "react";
import { StyleSheet, View } from "react-native";
import type { FaceLandmarker, FaceLandmarkerResult } from "@mediapipe/tasks-vision";

import type { FaceReading } from "../guidance";
import { lightOf } from "./light";
import type { GuideCameraProps } from "./types";

/**
 * The live front camera in a browser, with Google's MediaPipe face landmarker running in the
 * page (WebAssembly). Faces are found in the browser and never sent anywhere; only the picture
 * the client takes is uploaded. The files are served by the app itself from /face
 * (scripts/face-assets.mjs copies them at build time), and load only on this screen.
 */

/** Where the build puts the detector's files. */
const FACE_ASSETS = "/face";
/** How often a frame is read. More only costs battery. */
const READ_EVERY_MS = 120;

type Vision = typeof import("@mediapipe/tasks-vision");
/** How long the library may take to arrive before the client takes pictures with the button. */
const LOAD_WITHIN_MS = 20_000;

/**
 * Loads MediaPipe as a module from /face (see scripts/face-assets.mjs): the bundler cannot
 * follow the library's own dynamic imports, so it is not part of the app's bundle.
 */
function loadVision(): Promise<Vision> {
  const holder = globalThis as { finvestFaceVision?: Vision };
  if (holder.finvestFaceVision) return Promise.resolve(holder.finvestFaceVision);
  return new Promise<Vision>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("face detector did not load")), LOAD_WITHIN_MS);
    window.addEventListener(
      "finvest-face-vision",
      () => {
        clearTimeout(timer);
        if (holder.finvestFaceVision) resolve(holder.finvestFaceVision);
        else reject(new Error("face detector did not load"));
      },
      { once: true },
    );
    const script = document.createElement("script");
    script.type = "module";
    script.src = `${FACE_ASSETS}/loader.mjs`;
    script.onerror = () => {
      clearTimeout(timer);
      script.remove();
      reject(new Error("face detector did not load"));
    };
    document.head.appendChild(script);
  });
}

let landmarker: Promise<FaceLandmarker> | null = null;

function loadLandmarker(): Promise<FaceLandmarker> {
  if (!landmarker) {
    landmarker = (async () => {
      const { FaceLandmarker, FilesetResolver } = await loadVision();
      const files = await FilesetResolver.forVisionTasks(`${FACE_ASSETS}/wasm`);
      const options = (delegate: "GPU" | "CPU") => ({
        baseOptions: { modelAssetPath: `${FACE_ASSETS}/face_landmarker.task`, delegate },
        runningMode: "VIDEO" as const,
        numFaces: 2,
        outputFaceBlendshapes: true,
        outputFacialTransformationMatrixes: true,
      });
      try {
        return await FaceLandmarker.createFromOptions(files, options("GPU"));
      } catch {
        return await FaceLandmarker.createFromOptions(files, options("CPU"));
      }
    })();
    // A failed load can be tried again on the next visit.
    landmarker.catch(() => {
      landmarker = null;
    });
  }
  return landmarker;
}

/** Degrees from a column-major 4x4 transform: turning left or right, and nodding. */
function angles(matrix: number[]): { yaw: number; pitch: number } {
  const at = (row: number, col: number) => matrix[col * 4 + row] ?? 0;
  const degrees = 180 / Math.PI;
  return {
    yaw: Math.atan2(at(0, 2), at(2, 2)) * degrees,
    pitch: Math.asin(Math.max(-1, Math.min(1, -at(1, 2)))) * degrees,
  };
}

function blink(result: FaceLandmarkerResult, index: number, name: string): number | undefined {
  const score = result.faceBlendshapes[index]?.categories.find((category) => category.categoryName === name)?.score;
  return score === undefined ? undefined : 1 - score;
}

export function toReading(result: FaceLandmarkerResult, video: HTMLVideoElement, sample: HTMLCanvasElement): FaceReading {
  const faces = result.faceLandmarks;
  if (faces.length === 0) return { faces: 0 };
  // The largest face, by how wide its landmarks spread.
  const spans = faces.map((points) => {
    const xs = points.map((point) => point.x);
    const ys = points.map((point) => point.y);
    return { left: Math.min(...xs), right: Math.max(...xs), top: Math.min(...ys), bottom: Math.max(...ys) };
  });
  let index = 0;
  spans.forEach((span, i) => {
    const best = spans[index]!;
    if (span.right - span.left > best.right - best.left) index = i;
  });
  const box = spans[index]!;
  const matrix = result.facialTransformationMatrixes?.[index]?.data;
  return {
    faces: faces.length,
    width: box.right - box.left,
    offsetX: (box.left + box.right) / 2 - 0.5,
    offsetY: (box.top + box.bottom) / 2 - 0.5,
    ...(matrix ? angles(matrix) : {}),
    // The landmarker names eyes as seen in the picture; which is which does not matter here.
    leftEyeOpen: blink(result, index, "eyeBlinkLeft"),
    rightEyeOpen: blink(result, index, "eyeBlinkRight"),
    light: lightOf(video, sample, box),
  };
}

type DemoFace = (reading: FaceReading | null) => void;

export function GuideCamera({ active, fileName, onReading, onTrouble, style, ref }: GuideCameraProps) {
  const video = useRef<HTMLVideoElement | null>(null);
  const sample = useRef<HTMLCanvasElement | null>(null);
  const demo = useRef<FaceReading | null>(null);
  const reading = useRef(onReading);
  const trouble = useRef(onTrouble);
  useEffect(() => {
    reading.current = onReading;
    trouble.current = onTrouble;
  });

  // The camera, for as long as the screen is open.
  useEffect(() => {
    let stream: MediaStream | null = null;
    let gone = false;
    const media = typeof navigator === "undefined" ? undefined : navigator.mediaDevices;
    if (!media?.getUserMedia) {
      trouble.current("no_camera");
      return;
    }
    media
      .getUserMedia({ video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 960 } }, audio: false })
      .then((opened) => {
        if (gone) {
          opened.getTracks().forEach((track) => track.stop());
          return;
        }
        stream = opened;
        if (video.current) {
          video.current.srcObject = opened;
          void video.current.play().catch(() => undefined);
        }
      })
      .catch((error: unknown) => {
        const name = error instanceof DOMException ? error.name : "";
        trouble.current(name === "NotAllowedError" || name === "SecurityError" ? "no_permission" : "no_camera");
      });
    return () => {
      gone = true;
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  // A demo build lets screenshots and browser tests say what the camera sees, because the
  // browser's test camera has no face in it. Written inline, so a production build leaves it out.
  useEffect(() => {
    if (process.env.EXPO_PUBLIC_DEMO_AUTH !== "1") return;
    const holder = globalThis as { finvestDemoFace?: DemoFace };
    holder.finvestDemoFace = (next) => {
      demo.current = next;
    };
    return () => {
      delete holder.finvestDemoFace;
    };
  }, []);

  // The detector, read a few times a second while the camera is active.
  useEffect(() => {
    if (!active) return;
    let found: FaceLandmarker | null = null;
    let stopped = false;
    let last = -1;
    loadLandmarker().then(
      (loaded) => {
        found = loaded;
      },
      () => {
        if (!stopped && !demo.current) trouble.current("no_detector");
      },
    );
    const timer = setInterval(() => {
      if (demo.current) {
        reading.current(demo.current);
        return;
      }
      const element = video.current;
      if (!found || !element || element.readyState < 2 || !sample.current) return;
      const now = performance.now();
      if (now <= last) return;
      last = now;
      try {
        reading.current(toReading(found.detectForVideo(element, now), element, sample.current));
      } catch {
        // One bad frame is skipped; the next one is read.
      }
    }, READ_EVERY_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [active]);

  const capture = useCallback(async () => {
    const element = video.current;
    if (!element || element.videoWidth === 0) return null;
    const canvas = document.createElement("canvas");
    canvas.width = element.videoWidth;
    canvas.height = element.videoHeight;
    canvas.getContext("2d")?.drawImage(element, 0, 0);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
    if (!blob) return null;
    return { uri: URL.createObjectURL(blob), name: fileName, mimeType: "image/jpeg", size: blob.size };
  }, [fileName]);

  useImperativeHandle(ref, () => ({ capture }), [capture]);

  return (
    <View style={[styles.fill, style]}>
      <video
        ref={video}
        autoPlay
        muted
        playsInline
        aria-hidden
        // Shown as a mirror, as people expect of a selfie camera. The picture sent is not mirrored.
        style={{ width: "100%", height: "100%", objectFit: "cover", transform: "scaleX(-1)" }}
        data-testid="guide-video"
      />
      <canvas ref={sample} width={64} height={64} style={{ display: "none" }} />
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1, backgroundColor: "#000", overflow: "hidden" },
});
