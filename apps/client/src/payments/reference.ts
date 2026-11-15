/**
 * Short references a client can read out: "FV-3R0D5-3X44V" in place of a 20 character push ID,
 * which is case sensitive and can start with a dash.
 *
 * The same rule as the backend's app/shared/references.py: the push ID's last 8 characters
 * (48 random bits) written in Crockford base32, as two groups of five. Staff turn it back into
 * those 8 characters to find the record. Anything that is not a push ID is shown as it is.
 */
const PUSH_CHARS = "-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz";
const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const PUSH_ID = /^[-0-9A-Z_a-z]{20}$/;

export function shortReference(id: string, prefix = "FV"): string {
  if (!PUSH_ID.test(id)) return id;
  // 48 bits fit in a JavaScript number exactly (up to 2^53).
  let value = 0;
  for (const char of id.slice(-8)) value = value * 64 + PUSH_CHARS.indexOf(char);
  let code = "";
  for (let i = 0; i < 10; i += 1) {
    code = CROCKFORD[value % 32] + code;
    value = Math.floor(value / 32);
  }
  return `${prefix}-${code.slice(0, 5)}-${code.slice(5)}`;
}
