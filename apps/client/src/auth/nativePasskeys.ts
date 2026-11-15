type Json = Record<string, unknown>;

export type NativePasskeys = {
  isSupported(): boolean;
  create(request: Json): Promise<Json>;
  get(request: Json): Promise<Json>;
};

/** The phone's passkeys (react-native-passkey). nativePasskeys.web.ts keeps it out of the web build. */
export function loadNativePasskeys(): NativePasskeys | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return (require("react-native-passkey") as { Passkey: NativePasskeys }).Passkey;
  } catch {
    // Expo Go and other builds without the native module.
    return null;
  }
}
