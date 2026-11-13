import { AppState, AppStateStatus, Text } from "react-native";
import { act, fireEvent, render, screen } from "@testing-library/react-native";

import { AuthProvider, RETURN_TO_LIFETIME_MS, useAuth } from "../AuthProvider";
import { createStubAuthClient } from "../stubAuthClient";

function Probe() {
  const { returnTo, setReturnTo } = useAuth();
  return (
    <>
      <Text>{returnTo ?? "none"}</Text>
      <Text testID="ask" onPress={() => setReturnTo("/security/passkey")}>
        ask
      </Text>
    </>
  );
}

async function mount() {
  await render(
    <AuthProvider client={createStubAuthClient({ delayMs: 0 })}>
      <Probe />
    </AuthProvider>,
  );
}

describe("the return address after logging in again", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it("is kept for a log in that follows right away", async () => {
    await mount();
    await fireEvent.press(screen.getByTestId("ask"));
    await act(async () => jest.advanceTimersByTime(RETURN_TO_LIFETIME_MS - 1));
    expect(screen.getByText("/security/passkey")).toBeTruthy();
  });

  it("is dropped after five minutes, so a later log in goes Home", async () => {
    await mount();
    await fireEvent.press(screen.getByTestId("ask"));
    await act(async () => jest.advanceTimersByTime(RETURN_TO_LIFETIME_MS + 1));
    expect(screen.getByText("none")).toBeTruthy();
  });

  it("is dropped when the app goes to the background", async () => {
    let listener: ((state: AppStateStatus) => void) | undefined;
    const spy = jest.spyOn(AppState, "addEventListener").mockImplementation((_type, handler) => {
      listener = handler as (state: AppStateStatus) => void;
      return { remove: jest.fn() } as unknown as ReturnType<typeof AppState.addEventListener>;
    });
    await mount();
    await fireEvent.press(screen.getByTestId("ask"));
    expect(screen.getByText("/security/passkey")).toBeTruthy();
    await act(async () => listener?.("background"));
    expect(screen.getByText("none")).toBeTruthy();
    spy.mockRestore();
  });
});
