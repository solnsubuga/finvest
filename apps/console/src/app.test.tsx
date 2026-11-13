import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";
import { AuthError, type AuthApi } from "@/auth/api.ts";
import { AuthProvider } from "@/auth/AuthProvider.tsx";
import { createStubAuthApi } from "@/auth/stub.ts";
import { routes } from "@/routes.tsx";

function renderApp(path: string, api: AuthApi = createStubAuthApi()) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <AuthProvider api={api}>
      <RouterProvider router={router} />
    </AuthProvider>,
  );
  return { router, user: userEvent.setup() };
}

async function signIn(user: ReturnType<typeof userEvent.setup>, email: string) {
  await user.type(await screen.findByLabelText("Work email"), email);
  await user.type(screen.getByLabelText("Password"), "not-a-real-password");
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await user.click(await screen.findByRole("button", { name: "Use passkey" }));
}

describe("console shell", () => {
  it("sends a signed-out visitor to sign-in", async () => {
    const { router } = renderApp("/clients");
    expect(await screen.findByRole("heading", { name: "Staff sign-in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/sign-in");
  });

  it("signs in with password then passkey and returns to the page that was asked for", async () => {
    const { router, user } = renderApp("/clients");
    await signIn(user, "operations@finvest.test");

    expect(await screen.findByRole("heading", { level: 1, name: "Clients" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/clients");
    expect(screen.getByText("Grace Auma, Operations")).toBeInTheDocument();
    expect(screen.getAllByText("Signed in as").length).toBeGreaterThan(0);
  });

  it("does not create a session after the password step alone", async () => {
    const { user } = renderApp("/sign-in");
    await user.type(await screen.findByLabelText("Work email"), "admin@finvest.test");
    await user.type(screen.getByLabelText("Password"), "not-a-real-password");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { name: "Confirm it is you" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Console" })).not.toBeInTheDocument();
  });

  it("shows an error for a wrong email or password", async () => {
    const { user } = renderApp("/sign-in");
    await user.type(await screen.findByLabelText("Work email"), "stranger@finvest.test");
    await user.type(screen.getByLabelText("Password"), "not-a-real-password");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That email and password do not match a staff account.",
    );
  });

  it("asks for both fields before calling the service", async () => {
    const { user } = renderApp("/sign-in");
    await user.click(await screen.findByRole("button", { name: "Continue" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter your work email and your password.");
  });

  it("shows an error and lets the person retry when the passkey fails", async () => {
    const stub = createStubAuthApi();
    let failures = 1;
    const api: AuthApi = {
      ...stub,
      finishSignIn: (challenge, answer) =>
        failures-- > 0 ? Promise.reject(new AuthError("passkey_failed")) : stub.finishSignIn(challenge, answer),
    };
    const { user } = renderApp("/sign-in", api);
    await signIn(user, "admin@finvest.test");

    expect(await screen.findByRole("alert")).toHaveTextContent("We could not confirm your passkey.");
    await user.click(screen.getByRole("button", { name: "Use passkey" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
  });

  it("signs in with an authenticator code in place of a passkey", async () => {
    const { user } = renderApp("/sign-in");
    await user.type(await screen.findByLabelText("Work email"), "admin@finvest.test");
    await user.type(screen.getByLabelText("Password"), "not-a-real-password");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Use a code from your authenticator app" }));

    await user.type(screen.getByLabelText("6-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
  });

  it("goes straight to the code when the person has no passkey, and offers no passkey", async () => {
    const { user } = renderApp("/sign-in");
    await user.type(await screen.findByLabelText("Work email"), "finance@finvest.test");
    await user.type(screen.getByLabelText("Password"), "not-a-real-password");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByLabelText("6-digit code")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /passkey/i })).not.toBeInTheDocument();
  });

  it("offers no code to someone who only has a passkey", async () => {
    const { user } = renderApp("/sign-in");
    await user.type(await screen.findByLabelText("Work email"), "compliance@finvest.test");
    await user.type(screen.getByLabelText("Password"), "not-a-real-password");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("button", { name: "Use passkey" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /authenticator/i })).not.toBeInTheDocument();
  });

  it("shows an error for a wrong or short code and does not sign in", async () => {
    const { user } = renderApp("/sign-in");
    await user.type(await screen.findByLabelText("Work email"), "finance@finvest.test");
    await user.type(screen.getByLabelText("Password"), "not-a-real-password");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await user.type(await screen.findByLabelText("6-digit code"), "123");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter the 6-digit code from your authenticator app.");

    await user.clear(screen.getByLabelText("6-digit code"));
    await user.type(screen.getByLabelText("6-digit code"), "000000");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That code is not right.");
    expect(screen.queryByRole("navigation", { name: "Console" })).not.toBeInTheDocument();
  });

  it("blocks an account that has no passkey or authenticator app", async () => {
    const { user } = renderApp("/sign-in");
    await user.type(await screen.findByLabelText("Work email"), "invited@finvest.test");
    await user.type(screen.getByLabelText("Password"), "not-a-real-password");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("no passkey or authenticator app yet");
    expect(screen.queryByRole("heading", { name: "Confirm it is you" })).not.toBeInTheDocument();
  });

  it("never offers step 2 if the service returns no second step at all", async () => {
    const stub = createStubAuthApi();
    const api: AuthApi = { ...stub, startSignIn: () => Promise.resolve({ challengeId: "x", methods: [] }) };
    const { user } = renderApp("/sign-in", api);
    await user.type(await screen.findByLabelText("Work email"), "admin@finvest.test");
    await user.type(screen.getByLabelText("Password"), "not-a-real-password");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("no passkey or authenticator app yet");
    expect(screen.queryByRole("heading", { name: "Confirm it is you" })).not.toBeInTheDocument();
  });

  it("shows the menu for the person's role only", async () => {
    const { user } = renderApp("/sign-in");
    await signIn(user, "support@finvest.test");

    const nav = (await screen.findAllByRole("navigation", { name: "Console" }))[0]!;
    const links = within(nav)
      .getAllByRole("link")
      .map((link) => link.textContent);
    expect(links).toEqual(["Overview", "Support and Messages", "Complaints", "Clients"]);
    expect(within(nav).queryByText("Control")).not.toBeInTheDocument();
  });

  it("refuses a screen the role does not include, even by address", async () => {
    const { user } = renderApp("/settings");
    await signIn(user, "support@finvest.test");

    expect(await screen.findByRole("heading", { name: "You cannot open this screen" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Settings" })).not.toBeInTheDocument();
  });

  it("marks the current screen in the menu", async () => {
    const { user } = renderApp("/review-queue");
    await signIn(user, "admin@finvest.test");

    const nav = (await screen.findAllByRole("navigation", { name: "Console" }))[0]!;
    expect(within(nav).getByRole("link", { name: "Review Queue" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });

  it("signs out and returns to sign-in", async () => {
    const { router, user } = renderApp("/sign-in");
    await signIn(user, "admin@finvest.test");
    const nav = (await screen.findAllByRole("navigation", { name: "Console" }))[0]!;

    await user.click(within(nav).getByRole("button", { name: "Sign Out" }));
    expect(await screen.findByRole("heading", { name: "Staff sign-in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/sign-in");
  });

  it("says so when an address is not a console screen", async () => {
    const { user } = renderApp("/nowhere");
    await signIn(user, "admin@finvest.test");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});
