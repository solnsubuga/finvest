/**
 * Drawn stand-ins for the sample checks' photos, so the review screen can be seen with
 * pictures under `pnpm dev`. Not anyone's document or face.
 */

function svg(body: string): string {
  const doc = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200">${body}</svg>`;
  return `data:image/svg+xml,${encodeURIComponent(doc)}`;
}

const CARD = `<rect width="320" height="200" rx="14" fill="#e8eef0"/><rect x="0" y="0" width="320" height="34" rx="14" fill="#2f6f5e"/><rect x="0" y="20" width="320" height="14" fill="#2f6f5e"/>`;
const TEXT = `font-family="sans-serif" font-weight="700"`;

export const SAMPLE_PHOTOS = {
  front: svg(
    `${CARD}` +
      `<rect x="20" y="50" width="88" height="112" rx="6" fill="#b9c4c9"/><circle cx="64" cy="92" r="20" fill="#8e9ba2"/><ellipse cx="64" cy="150" rx="34" ry="24" fill="#8e9ba2"/>` +
      `<g ${TEXT} font-size="11" fill="#33403f"><text x="124" y="64">SURNAME</text><text x="124" y="98">GIVEN NAMES</text><text x="124" y="132">DATE OF BIRTH</text></g>` +
      `<g fill="#9aa8ad"><rect x="124" y="70" width="120" height="10" rx="3"/><rect x="124" y="104" width="90" height="10" rx="3"/><rect x="124" y="138" width="70" height="10" rx="3"/></g>`,
  ),
  back: svg(
    `${CARD}` +
      `<g fill="#33403f">${Array.from({ length: 34 }, (_, i) => `<rect x="${24 + i * 8}" y="56" width="${i % 3 === 0 ? 4 : 2}" height="54"/>`).join("")}</g>` +
      `<g fill="#9aa8ad"><rect x="24" y="128" width="272" height="10" rx="3"/><rect x="24" y="146" width="272" height="10" rx="3"/><rect x="24" y="164" width="200" height="10" rx="3"/></g>`,
  ),
  /** The same grey outline the development liveness stub hands back as its capture. */
  selfie: svg(
    `<rect width="320" height="200" fill="#e2e6ea"/><circle cx="160" cy="70" r="36" fill="#96a0aa"/><rect x="149" y="100" width="22" height="20" fill="#96a0aa"/><ellipse cx="160" cy="205" rx="82" ry="88" fill="#96a0aa"/>`,
  ),
} as const;
