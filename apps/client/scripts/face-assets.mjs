// Copies the guided selfie's face detector into public/face/, which the web build serves at
// /face: the MediaPipe library and WebAssembly runtime from node_modules, and the model kept in
// face-model/. Also copies Regula's liveness web component into public/regula/.
// See face-model/README.md. Run before `expo export --platform web` and `expo start --web`.
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const MODEL_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff";
const RUNTIME = [
  "vision_wasm_internal.js",
  "vision_wasm_internal.wasm",
  "vision_wasm_nosimd_internal.js",
  "vision_wasm_nosimd_internal.wasm",
];

const app = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(app, "public", "face");
const require = createRequire(join(app, "package.json"));
const wasm = join(dirname(require.resolve("@mediapipe/tasks-vision/vision_wasm_internal.js")));

const model = join(app, "face-model", "face_landmarker.task");
const found = createHash("sha256").update(readFileSync(model)).digest("hex");
if (found !== MODEL_SHA256) {
  console.error(`face-model/face_landmarker.task has SHA-256 ${found}, expected ${MODEL_SHA256}`);
  process.exit(1);
}

mkdirSync(join(out, "wasm"), { recursive: true });
copyFileSync(model, join(out, "face_landmarker.task"));
for (const file of RUNTIME) copyFileSync(join(wasm, file), join(out, "wasm", file));
// The library itself is loaded by the page as a module from /face, not bundled: the bundler
// cannot follow its own dynamic imports. loader.mjs hands it to the app once it has loaded.
copyFileSync(join(wasm, "..", "vision_bundle.mjs"), join(out, "vision_bundle.mjs"));
writeFileSync(
  join(out, "loader.mjs"),
  'import * as vision from "./vision_bundle.mjs";\n' +
    "globalThis.finvestFaceVision = vision;\n" +
    'globalThis.dispatchEvent(new Event("finvest-face-vision"));\n',
);
console.log(`Face detector copied to ${out}`);

// Regula's liveness web component (docs/identity-checks.md#regula-liveness), served at /regula
// and loaded only on the live selfie. It fetches its own WebAssembly worker from Regula.
const regula = join(app, "public", "regula");
mkdirSync(regula, { recursive: true });
copyFileSync(
  // The package exports only its ES module, so its script is read from the package's folder.
  join(app, "node_modules", "@regulaforensics", "vp-frontend-face-components", "dist", "main.iife.js"),
  join(regula, "face-components.js"),
);
console.log(`Regula liveness component copied to ${regula}`);
