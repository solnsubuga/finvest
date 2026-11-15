import { render, screen } from "@testing-library/react";
import { Component, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { isLoadError, ScreenLoader } from "./ScreenLoader.tsx";

function Throws({ error }: { error: Error }): ReactNode {
  throw error;
}

class Outer extends Component<
  { children: ReactNode },
  { caught: string | null }
> {
  state = { caught: null as string | null };
  static getDerivedStateFromError(error: Error) {
    return { caught: error.message };
  }
  render() {
    return this.state.caught ? (
      <p>Outer caught: {this.state.caught}</p>
    ) : (
      this.props.children
    );
  }
}

describe("ScreenLoader", () => {
  afterEach(() => vi.restoreAllMocks());

  it("says the screen did not load when its file could not be fetched", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const error = new TypeError(
      "Failed to fetch dynamically imported module: /assets/Clients-abc.js",
    );
    render(
      <ScreenLoader>
        <Throws error={error} />
      </ScreenLoader>,
    );
    expect(
      screen.getByRole("heading", { name: "This screen did not load" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Load the page again" }),
    ).toBeInTheDocument();
  });

  it("passes any other error on instead of saying nothing was changed", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <Outer>
        <ScreenLoader>
          <Throws error={new Error("Cannot read properties of undefined")} />
        </ScreenLoader>
      </Outer>,
    );
    expect(
      screen.queryByText("This screen did not load"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("Outer caught: Cannot read properties of undefined"),
    ).toBeInTheDocument();
  });

  it("knows a failed screen fetch in each browser's words", () => {
    expect(
      isLoadError(
        new TypeError("Failed to fetch dynamically imported module: x"),
      ),
    ).toBe(true);
    expect(
      isLoadError(
        new TypeError("error loading dynamically imported module: x"),
      ),
    ).toBe(true);
    expect(
      isLoadError(new TypeError("Importing a module script failed.")),
    ).toBe(true);
    expect(isLoadError(new Error("Request failed with status 500"))).toBe(
      false,
    );
  });
});
