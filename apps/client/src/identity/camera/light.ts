/**
 * Light over the face, read from the picture's pixels where the platform lets us (the web).
 * The numbers feed `guide` in ../guidance.ts.
 */

export type Light = { mean: number; glare: number; sharpness: number };

/** Brightness at or above this counts as almost white. */
const WHITE = 250;

/**
 * From RGBA pixels: the mean brightness (0 to 255), the share of almost white pixels, and the
 * mean strength of the edges (a Laplacian), which drops when the picture is blurred.
 */
export function lightStats(rgba: ArrayLike<number>, width: number, height: number): Light {
  const count = width * height;
  if (count === 0) return { mean: 0, glare: 0, sharpness: 0 };
  const luma = new Float32Array(count);
  let total = 0;
  let white = 0;
  for (let i = 0; i < count; i += 1) {
    const value = 0.299 * (rgba[i * 4] ?? 0) + 0.587 * (rgba[i * 4 + 1] ?? 0) + 0.114 * (rgba[i * 4 + 2] ?? 0);
    luma[i] = value;
    total += value;
    if (value >= WHITE) white += 1;
  }
  let edges = 0;
  let inner = 0;
  for (let y = 1; y < height - 1; y += 1) {
    for (let x = 1; x < width - 1; x += 1) {
      const i = y * width + x;
      const laplacian = 4 * luma[i]! - luma[i - 1]! - luma[i + 1]! - luma[i - width]! - luma[i + width]!;
      edges += Math.abs(laplacian);
      inner += 1;
    }
  }
  return { mean: total / count, glare: white / count, sharpness: inner === 0 ? 0 : edges / inner };
}

/** The face's box in shares of the picture, from the detector. */
export type Box = { left: number; right: number; top: number; bottom: number };

/** Draws the face from the video into the small `sample` canvas and reads its light. */
export function lightOf(video: HTMLVideoElement, sample: HTMLCanvasElement, box: Box): Light | undefined {
  const context = sample.getContext("2d", { willReadFrequently: true });
  if (!context || video.videoWidth === 0) return undefined;
  const left = Math.max(0, box.left) * video.videoWidth;
  const top = Math.max(0, box.top) * video.videoHeight;
  const width = (Math.min(1, box.right) - Math.max(0, box.left)) * video.videoWidth;
  const height = (Math.min(1, box.bottom) - Math.max(0, box.top)) * video.videoHeight;
  if (width < 8 || height < 8) return undefined;
  context.drawImage(video, left, top, width, height, 0, 0, sample.width, sample.height);
  const pixels = context.getImageData(0, 0, sample.width, sample.height);
  return lightStats(pixels.data, sample.width, sample.height);
}
