import { Platform } from "react-native";
import { loadNativePasskeys, type NativePasskeys } from "./nativePasskeys";

/**
 * Passkeys. The API sends the options as JSON with base64url strings where
 * WebAuthn wants bytes, and wants the credential back in the same JSON form.
 *
 * On the web this converts between the two and asks the browser's WebAuthn.
 * On Android and iPhone, react-native-passkey takes and gives that JSON form
 * as it is, and asks the phone's own passkeys (Credential Manager on Android,
 * AuthenticationServices on iPhone). The phone only lets the app use passkeys
 * for finvestug.com once finvestug.com says the app is Finvest's, in the
 * association files the website serves, so a phone build offers passkeys only
 * when it is built with `EXPO_PUBLIC_NATIVE_PASSKEYS=1`. Without it,
 * `passkeysSupported()` is false and the app offers the authenticator app or
 * an SMS code instead.
 */

type Json = Record<string, unknown>;

/** The native module, loaded only on a phone. The web build has its own file with none. */
function nativePasskeys(): NativePasskeys | null {
  if (Platform.OS === "web" || process.env.EXPO_PUBLIC_NATIVE_PASSKEYS !== "1") return null;
  return loadNativePasskeys();
}

/**
 * The module's errors carry a name in `error`. Closing the prompt, and a
 * phone that holds no Finvest passkey, both mean nothing was used.
 */
function nativeCancel(error: unknown): boolean {
  const name = typeof error === "object" && error !== null ? (error as { error?: unknown }).error : undefined;
  return name === "UserCancelled" || name === "NoCredentials" || name === "Interrupted" || name === "TimedOut";
}

async function askThePhone(work: () => Promise<Json>): Promise<Json> {
  try {
    return await work();
  } catch (error) {
    if (nativeCancel(error)) throw new PasskeyCancelled();
    throw error instanceof Error ? error : new Error("passkey");
  }
}

export class PasskeyCancelled extends Error {
  constructor() {
    super("cancelled");
    this.name = "PasskeyCancelled";
  }
}

export function passkeysSupported(): boolean {
  if (Platform.OS !== "web") {
    const native = nativePasskeys();
    try {
      return native !== null && native.isSupported();
    } catch {
      return false;
    }
  }
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
  const native = nativePasskeys();
  if (native) return askThePhone(() => native.create(options));
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
  const native = nativePasskeys();
  if (native) return askThePhone(() => native.get(options));
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
