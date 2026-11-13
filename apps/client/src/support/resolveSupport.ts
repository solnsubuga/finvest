import { SupportClient, SupportError } from "./types";

type StubModule = typeof import("./stubSupportClient");

/**
 * The in-memory demo shows notifications and replies that nobody sent, so it
 * must never be what a real client reaches. It follows the demo sign-in:
 * loaded in development, and in a production build only when
 * EXPO_PUBLIC_DEMO_AUTH=1 was set on purpose at build time.
 *
 * The condition is written inline, and the stub is required inside it, so the
 * bundler can see it is false in a normal production build and leave the stub
 * out of the bundle altogether. CI checks that it did.
 */
function loadStub(): StubModule | null {
  if (__DEV__ || process.env.EXPO_PUBLIC_DEMO_AUTH === "1") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubSupportClient") as StubModule;
  }
  return null;
}

/**
 * Chooses what the screens talk to for one signed-in account. Without the
 * stub, every call says Finvest cannot be reached.
 *
 * In the demo each account gets its own memory, so signing in as someone else
 * never shows the last person's chat. The two demo accounts start with the
 * canvas's notifications. An account made by signing up starts empty.
 */
export function resolveSupportClient(
  account: string | null,
  stub: StubModule | null = loadStub(),
): SupportClient {
  if (!stub || !account) {
    return unavailableSupportClient;
  }
  return stub.createStubSupportClient({ empty: !stub.SEEDED_ACCOUNTS.includes(account) });
}

const refuse = async (): Promise<never> => {
  throw new SupportError("unavailable");
};

const unavailableSupportClient: SupportClient = {
  listNotifications: refuse,
  markNotificationsRead: refuse,
  getPreferences: refuse,
  savePreferences: refuse,
  getChat: refuse,
  sendMessage: refuse,
  markChatRead: refuse,
  listComplaints: refuse,
  getComplaint: refuse,
  makeComplaint: refuse,
};

/**
 * The number "Call us" dials, set at build time. Finvest has not published a
 * support line yet, so without it the button is not shown.
 */
export function supportPhone(): string | null {
  const value = process.env.EXPO_PUBLIC_SUPPORT_PHONE;
  return value && /^\+[0-9]{9,15}$/.test(value) ? value : null;
}
