/**
 * The files that tell Android and iPhone the Finvest app may use passkeys made for
 * finvestug.com (docs/sign-in.md, "Passkeys in the phone apps"). Without them the phone
 * refuses the app's passkey requests, so the app is built to offer passkeys only once they
 * are served.
 *
 * The app's ids are fixed in apps/client/app.json. The Apple team and the Android signing
 * certificate are given at build time (PUBLIC_APPLE_TEAM_ID, PUBLIC_ANDROID_CERT_SHA256).
 * When one is not given, its file lists no app, which vouches for nothing.
 */

export const APP_ID = "com.finvestug.app";

const TEAM = /^[A-Z0-9]{10}$/;
const FINGERPRINT = /^[0-9A-F]{2}(:[0-9A-F]{2}){31}$/;

/** `apple-app-site-association`: the iPhone app may use this site's passkeys. */
export function appleAssociation(teamId: string | undefined): object {
  const team = (teamId ?? "").trim();
  if (team && !TEAM.test(team)) throw new Error("PUBLIC_APPLE_TEAM_ID must be the 10 character Apple team ID");
  return { webcredentials: { apps: team ? [`${team}.${APP_ID}`] : [] } };
}

/** `assetlinks.json`: the Android app signed with these certificates may use them too. */
export function androidAssetLinks(fingerprints: string | undefined): object[] {
  const certs = (fingerprints ?? "")
    .split(/[\s,]+/)
    .map((value) => value.trim().toUpperCase())
    .filter(Boolean);
  for (const cert of certs) {
    if (!FINGERPRINT.test(cert)) {
      throw new Error("PUBLIC_ANDROID_CERT_SHA256 must be SHA-256 fingerprints like AB:CD:..., comma separated");
    }
  }
  if (certs.length === 0) return [];
  return [
    {
      relation: ["delegate_permission/common.get_login_creds"],
      target: { namespace: "android_app", package_name: APP_ID, sha256_cert_fingerprints: certs },
    },
  ];
}
