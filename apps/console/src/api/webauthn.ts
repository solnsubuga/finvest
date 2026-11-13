/**
 * Passkeys in the browser. The API sends WebAuthn options as JSON with every
 * binary field in base64url, and takes the credential back in the same JSON
 * form (`PublicKeyCredential.toJSON()`). Browsers that cannot parse or write
 * that JSON themselves get the conversion done here.
 */

type Json = Record<string, unknown>;

export function fromBase64Url(value: string): ArrayBuffer {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  const bytes = Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
  return bytes.buffer;
}

export function toBase64Url(buffer: ArrayBuffer): string {
  let text = "";
  for (const byte of new Uint8Array(buffer)) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function withIds(list: unknown): PublicKeyCredentialDescriptor[] | undefined {
  if (!Array.isArray(list)) return undefined;
  return list.map((item: Json) => ({ ...item, id: fromBase64Url(String(item.id)) }) as PublicKeyCredentialDescriptor);
}

export function creationOptions(json: Json): PublicKeyCredentialCreationOptions {
  const user = json.user as Json;
  return {
    ...(json as object),
    challenge: fromBase64Url(String(json.challenge)),
    user: { ...(user as object), id: fromBase64Url(String(user.id)) },
    excludeCredentials: withIds(json.excludeCredentials),
  } as PublicKeyCredentialCreationOptions;
}

export function requestOptions(json: Json): PublicKeyCredentialRequestOptions {
  return {
    ...(json as object),
    challenge: fromBase64Url(String(json.challenge)),
    allowCredentials: withIds(json.allowCredentials),
  } as PublicKeyCredentialRequestOptions;
}

/** The credential as the API reads it. */
export function credentialJson(credential: PublicKeyCredential): Json {
  const withJson = credential as PublicKeyCredential & { toJSON?: () => Json };
  if (typeof withJson.toJSON === "function") return withJson.toJSON();
  const response = credential.response;
  const encoded: Json = { clientDataJSON: toBase64Url(response.clientDataJSON) };
  if ("attestationObject" in response) {
    const attestation = response as AuthenticatorAttestationResponse;
    encoded.attestationObject = toBase64Url(attestation.attestationObject);
    encoded.transports = attestation.getTransports?.() ?? [];
  } else {
    const assertion = response as AuthenticatorAssertionResponse;
    encoded.authenticatorData = toBase64Url(assertion.authenticatorData);
    encoded.signature = toBase64Url(assertion.signature);
    encoded.userHandle = assertion.userHandle ? toBase64Url(assertion.userHandle) : null;
  }
  return {
    id: credential.id,
    rawId: toBase64Url(credential.rawId),
    type: credential.type,
    response: encoded,
    clientExtensionResults: credential.getClientExtensionResults(),
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
  };
}

export interface PasskeyBrowser {
  create(options: Json): Promise<Json>;
  get(options: Json): Promise<Json>;
}

/** The real browser. A cancelled or failed prompt rejects. */
export const browserPasskeys: PasskeyBrowser = {
  async create(options) {
    const credential = await navigator.credentials.create({ publicKey: creationOptions(options) });
    if (!credential) throw new Error("no credential");
    return credentialJson(credential as PublicKeyCredential);
  },
  async get(options) {
    const credential = await navigator.credentials.get({ publicKey: requestOptions(options) });
    if (!credential) throw new Error("no credential");
    return credentialJson(credential as PublicKeyCredential);
  },
};
