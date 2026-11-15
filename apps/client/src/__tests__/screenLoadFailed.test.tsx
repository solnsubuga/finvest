import { act, fireEvent, render, screen } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { isLoadError, ScreenLoadFailed } from "@/navigation/ScreenLoadFailed";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

beforeAll(warmRouter, WARM_ROUTER_MS);

function asyncRequireError(): Error {
  const error = new Error("Loading module https://app.finvestug.com/_expo/static/js/web/invest-1.js failed.");
  error.name = "AsyncRequireError";
  return error;
}

describe("a screen that does not load", () => {
  it("tells a failed screen fetch from a screen that broke", () => {
    expect(isLoadError(asyncRequireError())).toBe(true);
    expect(isLoadError(new TypeError("Failed to fetch dynamically imported module"))).toBe(true);
    expect(isLoadError(new Error("Cannot read properties of undefined"))).toBe(false);
  });

  it("says the screen did not load and offers to try again, instead of a blank page", async () => {
    await render(<ScreenLoadFailed error={asyncRequireError()} retry={async () => undefined} />);
    expect(screen.getByText("This screen did not load")).toBeTruthy();
    expect(screen.getByTestId("screen-load-retry")).toBeTruthy();
  });

  it("draws a broken screen again when the client tries again", async () => {
    const retry = jest.fn(async () => undefined);
    await render(<ScreenLoadFailed error={new Error("boom")} retry={retry} />);
    expect(screen.getByText("Something went wrong")).toBeTruthy();
    await act(async () => fireEvent.press(screen.getByTestId("screen-load-retry")));
    expect(retry).toHaveBeenCalledTimes(1);
  });
});

describe("an address with no screen", () => {
  it("shows Finvest's own page with a way back, not the framework's", async () => {
    await renderRouter("./src/app", { initialUrl: "/this-page-does-not-exist" });
    expect(await screen.findByText("We could not find that page")).toBeTruthy();
    expect(screen.queryByText(/Unmatched Route/i)).toBeNull();
    await act(async () => fireEvent.press(screen.getByTestId("not-found-home")));
    expect(screen).toHavePathname("/welcome");
  });

  it("does not list every screen at /_sitemap", async () => {
    await renderRouter("./src/app", { initialUrl: "/_sitemap" });
    expect(await screen.findByText("We could not find that page")).toBeTruthy();
  });
});
