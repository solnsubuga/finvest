/**
 * Passkeys on Android and iPhone go through react-native-passkey, which takes
 * the API's JSON options as they are and gives back the JSON the API reads.
 */

const mockNative = {
  supported: true,
  create: jest.fn(),
  get: jest.fn(),
};

jest.mock("react-native-passkey", () => ({
  Passkey: {
    isSupported: () => mockNative.supported,
    create: (request: unknown) => mockNative.create(request),
    get: (request: unknown) => mockNative.get(request),
  },
}));

jest.mock("react-native", () => ({ Platform: { OS: "ios" } }));

const OPTIONS = { challenge: "abc", rpId: "finvestug.com", allowCredentials: [], userVerification: "required" };

function load() {
  let loaded!: typeof import("../webauthn");
  jest.isolateModules(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    loaded = require("../webauthn");
  });
  return loaded;
}

beforeEach(() => {
  process.env.EXPO_PUBLIC_NATIVE_PASSKEYS = "1";
  mockNative.supported = true;
  mockNative.create.mockReset();
  mockNative.get.mockReset();
});

afterAll(() => {
  delete process.env.EXPO_PUBLIC_NATIVE_PASSKEYS;
});

describe("passkeys on a phone", () => {
  it("are offered when the build says the website vouches for the app and the phone can", () => {
    expect(load().passkeysSupported()).toBe(true);
    mockNative.supported = false;
    expect(load().passkeysSupported()).toBe(false);
  });

  it("are not offered in a build made before the website vouches for the app", () => {
    delete process.env.EXPO_PUBLIC_NATIVE_PASSKEYS;
    expect(load().passkeysSupported()).toBe(false);
  });

  it("hand the API's options to the phone unchanged and return its answer unchanged", async () => {
    const answer = { id: "c", rawId: "c", type: "public-key", response: { clientDataJSON: "x", signature: "s" } };
    mockNative.get.mockResolvedValue(answer);
    expect(await load().signWithPasskey(OPTIONS)).toBe(answer);
    expect(mockNative.get).toHaveBeenCalledWith(OPTIONS);

    const created = { id: "n", rawId: "n", type: "public-key", response: { attestationObject: "a" } };
    mockNative.create.mockResolvedValue(created);
    expect(await load().createPasskey({ challenge: "abc", user: { id: "u" } })).toBe(created);
  });

  it.each(["UserCancelled", "NoCredentials", "Interrupted", "TimedOut"])(
    "treat %s as nothing used",
    async (name) => {
      const webauthn = load();
      mockNative.get.mockRejectedValue({ error: name, message: "x" });
      await expect(webauthn.signWithPasskey(OPTIONS)).rejects.toBeInstanceOf(webauthn.PasskeyCancelled);
    },
  );

  it("pass on any other failure as a failure, not a cancel", async () => {
    const webauthn = load();
    mockNative.get.mockRejectedValue({ error: "BadConfiguration", message: "x" });
    const failure = await webauthn.signWithPasskey(OPTIONS).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(Error);
    expect(failure).not.toBeInstanceOf(webauthn.PasskeyCancelled);
  });
});
