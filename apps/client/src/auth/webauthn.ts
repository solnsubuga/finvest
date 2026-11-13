import { Platform } from "react-native";

/**
 * Passkeys through the browser's WebAuthn API. The API sends the options as
 * JSON with base64url strings where WebAuthn wants bytes, and wants the
 * credential back in the same JSON form, so this file only converts between
 * the two and asks the browser.
 *
 * A phone build cannot use the browser's API. Passkeys there need a native
 * module and a development build, so `passkeysSupported()` is false and the
 * app offers the authenticator app or an SMS code instead.
 */

type Json = Record<string, unknown>;

export class PasskeyCancelled extends Error {
  constructor() {
    super("cancelled");
    this.name = "PasskeyCancelled";
  }
}

export function passkeysSupported(): boolean {
  return (
    Platform.OS === "web" &&
    typeof window !== "undefined" &&
    typeof window.PublicKeyCredential === "function" &&
    typeof navigator !== "undefined" &&
    typeof navigator.credentials?.create === "function"
  );
}

function fromBase64url(value: string): ArrayBuffer {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes.buffer;
}

function toBase64url(buffer: ArrayBuffer | null | undefined): string | undefined {
  if (!buffer) return undefined;
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function descriptors(list: unknown): PublicKeyCredentialDescriptor[] | undefined {
  if (!Array.isArray(list)) return undefined;
  return (list as Json[]).map((item) => ({
    ...(item as object),
    type: "public-key",
    id: fromBase64url(String(item.id)),
  })) as PublicKeyCredentialDescriptor[];
}

function creationOptions(json: Json): PublicKeyCredentialCreationOptions {
  const user = json.user as Json;
  return {
    ...(json as object),
    challenge: fromBase64url(String(json.challenge)),
    user: { ...(user as object), id: fromBase64url(String(user.id)) },
    excludeCredentials: descriptors(json.excludeCredentials),
  } as PublicKeyCredentialCreationOptions;
}

function requestOptions(json: Json): PublicKeyCredentialRequestOptions {
  return {
    ...(json as object),
    challenge: fromBase64url(String(json.challenge)),
    allowCredentials: descriptors(json.allowCredentials),
  } as PublicKeyCredentialRequestOptions;
}

/** The browser throws NotAllowedError when the person dismisses the prompt or it times out. */
function isCancel(error: unknown): boolean {
  return error instanceof DOMException && (error.name === "NotAllowedError" || error.name === "AbortError");
}

/** Makes a new passkey on this device and returns it in the JSON form the API reads. */
export async function createPasskey(options: Json): Promise<Json> {
  let credential: PublicKeyCredential | null;
  try {
    credential = (await navigator.credentials.create({ publicKey: creationOptions(options) })) as PublicKeyCredential | null;
  } catch (error) {
    if (isCancel(error)) throw new PasskeyCancelled();
    throw error;
  }
  if (!credential) throw new PasskeyCancelled();
  const response = credential.response as AuthenticatorAttestationResponse;
  return {
    id: credential.id,
    rawId: toBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: toBase64url(response.clientDataJSON),
      attestationObject: toBase64url(response.attestationObject),
      transports: typeof response.getTransports === "function" ? response.getTransports() : undefined,
    },
    clientExtensionResults: credential.getClientExtensionResults(),
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
  };
}

/** Signs the API's challenge with a passkey already on this device. */
export async function signWithPasskey(options: Json): Promise<Json> {
  let credential: PublicKeyCredential | null;
  try {
    credential = (await navigator.credentials.get({ publicKey: requestOptions(options) })) as PublicKeyCredential | null;
  } catch (error) {
    if (isCancel(error)) throw new PasskeyCancelled();
    throw error;
  }
  if (!credential) throw new PasskeyCancelled();
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    id: credential.id,
    rawId: toBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: toBase64url(response.clientDataJSON),
      authenticatorData: toBase64url(response.authenticatorData),
      signature: toBase64url(response.signature),
      userHandle: toBase64url(response.userHandle),
    },
    clientExtensionResults: credential.getClientExtensionResults(),
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
  };
}
