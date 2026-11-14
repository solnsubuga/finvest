# Face landmarker model

`face_landmarker.task` is Google's MediaPipe Face Landmarker model (float16, version 1), used by
the guided selfie on the web (`src/identity/camera/GuideCamera.web.tsx`). It runs in the browser;
nothing is sent to Google. It is published under the Apache License 2.0 at
https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task

`scripts/face-assets.mjs` checks its SHA-256 below and copies it, with the WebAssembly runtime from
`@mediapipe/tasks-vision`, to `public/face/` before a web build. To update it, download the new
file, put its SHA-256 in the script, and say so in the PR.
