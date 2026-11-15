// Authenticator app codes (RFC 6238, SHA-1, 6 digits, 30 seconds) for the demo staff, at the
// story clock, so their second step can be typed on camera.
import { createHmac } from "node:crypto";

function base32(secret) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secret.replace(/[\s=]/g, "").toUpperCase()) bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

export function totp(secret, atMs = Date.now()) {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(atMs / 1000 / 30)));
  const mac = createHmac("sha1", base32(secret)).update(counter).digest();
  const o = mac[mac.length - 1] & 0xf;
  const n = ((mac[o] & 0x7f) << 24) | (mac[o + 1] << 16) | (mac[o + 2] << 8) | mac[o + 3];
  return String(n % 1_000_000).padStart(6, "0");
}

// A code with at least `margin` seconds of life left, so it is not stale by the time it lands.
export async function freshTotp(secret, nowMs, margin = 8) {
  const left = 30 - (Math.floor(nowMs / 1000) % 30);
  if (left < margin) await new Promise((r) => setTimeout(r, (left + 1) * 1000));
  return totp(secret, nowMs + (left < margin ? (left + 1) * 1000 : 0));
}
