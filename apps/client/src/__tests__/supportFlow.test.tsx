import { act, fireEvent, screen, waitFor } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { DEMO_REPLY } from "@/support/stubSupportClient";
import { SupportError } from "@/support/types";

/** The demo's clock, moved by hand so the reply can be waited for. */
const mockClock = { ms: Date.parse("2026-11-13T12:40:00Z") };
const mockSend = jest.fn();
const mockSave = jest.fn();

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return {
    ...actual,
    createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }),
  };
});

jest.mock("@/support/stubSupportClient", () => {
  const actual = jest.requireActual("@/support/stubSupportClient");
  return {
    ...actual,
    createStubSupportClient: () => {
      const client = actual.createStubSupportClient({ delayMs: 0, now: () => mockClock.ms });
      return {
        ...client,
        sendMessage: async (message: unknown) => {
          mockSend(message);
          return client.sendMessage(message);
        },
        savePreferences: async (category: unknown, channels: unknown) => {
          mockSave(category, channels);
          return client.savePreferences(category, channels);
        },
      };
    },
  };
});

// The preset's mock of the native module returns nothing. Tests need real, distinct keys.
jest.mock("expo-crypto", () => ({
  randomUUID: () => jest.requireActual("node:crypto").randomUUID(),
}));

jest.mock("@/support/pollInterval", () => ({ CHAT_POLL_MS: 25 }));

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

async function signIn() {
  await renderRouter("./src/app", { initialUrl: "/" });
  await fireEvent.press(await screen.findByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "772123456");
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await fireEvent.press(await screen.findByTestId("use-passkey"));
  await screen.findByText("Welcome, Amara");
}

async function openHelp() {
  await signIn();
  await fireEvent.press(screen.getByLabelText("Profile"));
  await fireEvent.press(await screen.findByTestId("profile-help"));
  await screen.findByText("Write to us here");
}

beforeEach(() => {
  mockSend.mockClear();
  mockSave.mockClear();
  mockClock.ms = Date.parse("2026-11-13T12:40:00Z");
});

// The first test in a file renders the whole router from cold, which takes several seconds on
// a busy CI runner. The assertions are unchanged; only the time allowed to reach them is.
jest.setTimeout(30_000);

describe("the notification centre", () => {
  it("shows the unread count on Home and the list behind it", async () => {
    await signIn();
    await fireEvent.press(await screen.findByLabelText("Notifications, 2 unread"));

    await screen.findByText("Investment started");
    expect(screen.getByText("You earned UGX 2,416 today")).toBeTruthy();
    expect(screen.getByText("14:40")).toBeTruthy();
    expect(
      screen.getByText("You choose the channel for each kind of alert: push, SMS or email."),
    ).toBeTruthy();
  });

  it("marks everything read", async () => {
    await signIn();
    await fireEvent.press(await screen.findByLabelText("Notifications, 2 unread"));
    await fireEvent.press(await screen.findByText("Mark all as read"));

    await waitFor(() => expect(screen.queryByText("Mark all as read")).toBeNull());
    expect(screen.queryByLabelText(/^Unread\./)).toBeNull();
    await fireEvent.press(screen.getByLabelText("Back"));
    await screen.findByLabelText("Notifications");
  });

  it("opens the screen a notification points to and marks it read", async () => {
    await signIn();
    await fireEvent.press(await screen.findByLabelText("Notifications, 2 unread"));
    await fireEvent.press(await screen.findByLabelText(/New sign-in to your account/));
    await screen.findByText("Sign in and security");
  });
});

describe("notification settings", () => {
  async function openSettings() {
    await signIn();
    await fireEvent.press(await screen.findByTestId("open-notifications"));
    await fireEvent.press(await screen.findByText("Settings"));
    await screen.findByText("Money in and out");
  }

  it("shows the defaults and saves a change as it is made", async () => {
    await openSettings();
    expect(screen.getByTestId("pref-money-sms").props.accessibilityState.checked).toBe(true);
    expect(screen.getByTestId("pref-money-email").props.accessibilityState.checked).toBe(false);

    await fireEvent.press(screen.getByTestId("pref-money-email"));
    await waitFor(() =>
      expect(screen.getByTestId("pref-money-email").props.accessibilityState.checked).toBe(true),
    );
    expect(mockSave).toHaveBeenCalledWith("money", ["push", "sms", "email"]);
  });

  it("keeps SMS or email on for security alerts without asking the server", async () => {
    await openSettings();
    await fireEvent.press(screen.getByTestId("pref-security-sms"));
    await waitFor(() =>
      expect(screen.getByTestId("pref-security-sms").props.accessibilityState.checked).toBe(false),
    );
    await fireEvent.press(screen.getByTestId("pref-security-email"));

    await screen.findByText(
      "Security alerts always go by SMS or email, so one of them stays on.",
    );
    expect(screen.getByTestId("pref-security-email").props.accessibilityState.checked).toBe(true);
    expect(mockSave).toHaveBeenCalledTimes(1);
  });

  it("leaves the switch as it was when a change cannot be saved", async () => {
    await openSettings();
    mockSave.mockImplementationOnce(() => {
      throw new SupportError("unavailable");
    });
    await fireEvent.press(screen.getByTestId("pref-news-push"));

    await screen.findByText("We could not reach Finvest. Check your connection and try again.");
    expect(screen.getByTestId("pref-news-push").props.accessibilityState.checked).toBe(true);
  });
});

describe("the help chat", () => {
  it("sends a message and shows the reply when it arrives", async () => {
    await openHelp();
    await fireEvent.changeText(screen.getByTestId("chat-input"), "My withdrawal is late");
    await fireEvent.press(screen.getByTestId("chat-send"));

    await screen.findByLabelText(/^You, 15:40: My withdrawal is late/);
    expect(screen.getByText("Today")).toBeTruthy();
    expect(screen.queryByText("Write to us here")).toBeNull();
    expect(screen.getByTestId("chat-input").props.value).toBe("");

    await act(async () => {
      mockClock.ms += 5_000;
    });
    await screen.findByText(DEMO_REPLY);
    expect(screen.getByText("Grace from Finvest")).toBeTruthy();
  });

  it("sends nothing for an empty message", async () => {
    await openHelp();
    await fireEvent.changeText(screen.getByTestId("chat-input"), "   ");
    await fireEvent.press(screen.getByTestId("chat-send"));
    expect(mockSend).not.toHaveBeenCalled();
  });

  it("keeps a message that failed and sends it again under the same key", async () => {
    await openHelp();
    mockSend.mockImplementationOnce(() => {
      throw new SupportError("unavailable");
    });
    await fireEvent.changeText(screen.getByTestId("chat-input"), "Hello");
    await fireEvent.press(screen.getByTestId("chat-send"));

    await fireEvent.press(await screen.findByTestId("retry-message"));
    await screen.findByLabelText(/^You, 15:40: Hello/);
    expect(screen.queryByTestId("retry-message")).toBeNull();
    expect(mockSend).toHaveBeenCalledTimes(2);
    expect(mockSend.mock.calls[1][0]).toEqual(mockSend.mock.calls[0][0]);
  });

  it("opens a quick answer, and files the chat under its topic", async () => {
    await openHelp();
    await fireEvent.press(screen.getByText("Withdrawal times"));
    await screen.findByText("How long a withdrawal takes");

    await fireEvent.press(screen.getByTestId("answer-to-chat"));
    await fireEvent.changeText(await screen.findByTestId("chat-input"), "Still waiting");
    await fireEvent.press(screen.getByTestId("chat-send"));
    await waitFor(() => expect(mockSend).toHaveBeenCalled());
    expect(mockSend.mock.calls[0][0].topic).toBe("withdrawal");
  });
});

describe("complaints", () => {
  async function openComplaints() {
    await openHelp();
    await fireEvent.press(screen.getByTestId("open-complaints"));
    await screen.findByText("Your complaints");
  }

  it("shows an earlier complaint with our answer", async () => {
    await openComplaints();
    await fireEvent.press(screen.getByTestId("complaint-C-1000"));
    await screen.findByText("We agreed with you");
    expect(screen.getByText("Closed")).toBeTruthy();
    expect(screen.getByText("Closed on")).toBeTruthy();
  });

  it("asks for every part before sending", async () => {
    await openComplaints();
    await fireEvent.press(screen.getByTestId("new-complaint"));
    await fireEvent.press(await screen.findByTestId("send-complaint"));

    await screen.findByText("Choose what it is about.");
    expect(screen.getByText("Say in one line what went wrong.")).toBeTruthy();
    expect(screen.getByText("Tell us what happened.")).toBeTruthy();

    // An error goes as soon as its field is put right.
    await fireEvent.press(screen.getByTestId("category-fees"));
    await fireEvent.changeText(screen.getByTestId("complaint-summary"), "Charged twice");
    expect(screen.queryByText("Choose what it is about.")).toBeNull();
    expect(screen.queryByText("Say in one line what went wrong.")).toBeNull();
    expect(screen.getByText("Tell us what happened.")).toBeTruthy();
  });

  it("gives a new complaint a reference and a date, and lists it", async () => {
    await openComplaints();
    await fireEvent.press(screen.getByTestId("new-complaint"));
    await fireEvent.press(await screen.findByTestId("category-withdrawals"));
    await fireEvent.changeText(screen.getByTestId("complaint-summary"), "My withdrawal is late");
    await fireEvent.changeText(screen.getByTestId("complaint-details"), "Asked on Tuesday.");
    await fireEvent.press(screen.getByTestId("send-complaint"));

    await screen.findByText("C-1001");
    expect(screen.getByText(/We have your complaint/)).toBeTruthy();
    expect(screen.getByText("Received")).toBeTruthy();
    expect(screen.getByText("Fri 20 Nov")).toBeTruthy();

    await fireEvent.press(screen.getByLabelText("Back"));
    await screen.findByTestId("complaint-C-1001");
    expect(screen.getByText("Received. Answer by Fri 20 Nov")).toBeTruthy();
  });
});
