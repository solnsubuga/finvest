import { lightStats } from "../camera/light";

function picture(width: number, height: number, value: (x: number, y: number) => number): Uint8ClampedArray {
  const rgba = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = (y * width + x) * 4;
      const v = value(x, y);
      rgba[i] = v;
      rgba[i + 1] = v;
      rgba[i + 2] = v;
      rgba[i + 3] = 255;
    }
  }
  return rgba;
}

describe("light over the face", () => {
  it("reads the mean brightness and the share of almost white pixels", () => {
    expect(lightStats(picture(8, 8, () => 40), 8, 8)).toMatchObject({ mean: 40, glare: 0, sharpness: 0 });
    const half = lightStats(picture(8, 8, (x) => (x < 4 ? 255 : 0)), 8, 8);
    expect(half.mean).toBeCloseTo(127.5, 0);
    expect(half.glare).toBe(0.5);
  });

  it("finds a sharp picture sharper than a smooth one", () => {
    const checks = lightStats(picture(16, 16, (x, y) => ((x + y) % 2 === 0 ? 200 : 60)), 16, 16);
    const smooth = lightStats(picture(16, 16, (x) => 60 + x * 4), 16, 16);
    expect(checks.sharpness).toBeGreaterThan(100);
    expect(smooth.sharpness).toBeLessThan(1);
  });

  it("reads nothing from an empty picture", () => {
    expect(lightStats([], 0, 0)).toEqual({ mean: 0, glare: 0, sharpness: 0 });
  });
});
