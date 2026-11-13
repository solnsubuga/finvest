import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";
import type { AuthApi } from "@/auth/api.ts";
import { AuthProvider } from "@/auth/AuthProvider.tsx";
import { createStubAuthApi, STUB_ENROLMENT_TOKEN, STUB_LEAKED_PASSWORD } from "@/auth/stub.ts";
import { routes } from "@/routes.tsx";

const GOOD_PASSWORD = "a long sample sentence";

function renderEnrol(hash: string, api: AuthApi = createStubAuthApi()) {
  const router = createMemoryRouter(routes, { initialEntries: [`/enrol${hash}`] });
  render(
    <AuthProvider api={api}>
      <RouterProvider router={router} />
    </AuthProvider>,
  );
  return { router, api, user: userEvent.setup() };
}

async function fillPasswords(user: ReturnType<typeof userEvent.setup>, password: string, again = password) {
  await user.type(await screen.findByLabelText("Password"), password);
  await user.type(screen.getByLabelText("Type it again"), again);
}

describe("staff set-up from a one-time link", () => {
  it("greets the invited person and takes the token out of the address", async () => {
    const { router } = renderEnrol(`#${STUB_ENROLMENT_TOKEN}`);
    expect(await screen.findByText(/Welcome, Sample Invitee/)).toBeInTheDocument();
    expect(router.state.location.hash).toBe("");
  });

  it("refuses a link with no token", async () => {
    renderEnrol("");
    expect(await screen.findByRole("heading", { name: "This link cannot be used" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("expired or has already been used");
  });

  it("refuses a token it does not know", async () => {
    renderEnrol("#not-a-real-token");
    expect(await screen.findByRole("heading", { name: "This link cannot be used" })).toBeInTheDocument();
  });

  it("sets up a passkey, signs the person in, and then the link is spent", async () => {
    const { router, api, user } = renderEnrol(`#${STUB_ENROLMENT_TOKEN}`);
    await fillPasswords(user, GOOD_PASSWORD);
    await user.click(screen.getByRole("radio", { name: /Passkey/ }));
    await user.click(screen.getByRole("button", { name: "Create passkey and finish" }));

    expect(await screen.findByText("Sample Invitee, Support")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/overview");
    await expect(api.getEnrolment(STUB_ENROLMENT_TOKEN)).rejects.toMatchObject({ code: "link_expired" });
    expect((await api.startSignIn("invited@finvest.test", "anything")).methods).toEqual(["passkey"]);
  });

  it("sets up an authenticator app with the QR code, the key and a first code", async () => {
    const { api, user } = renderEnrol(`#${STUB_ENROLMENT_TOKEN}`);
    await fillPasswords(user, GOOD_PASSWORD);
    await user.click(screen.getByRole("radio", { name: /Authenticator app/ }));

    expect(await screen.findByTitle("QR code for your authenticator app")).toBeInTheDocument();
    await user.type(screen.getByLabelText("6-digit code"), "123456");
    await user.click(screen.getByRole("button", { name: "Finish set-up" }));

    expect(await screen.findByText("Sample Invitee, Support")).toBeInTheDocument();
    expect((await api.startSignIn("invited@finvest.test", "anything")).methods).toEqual(["authenticator"]);
  });

  it("does not finish without a second step chosen", async () => {
    const { api, user } = renderEnrol(`#${STUB_ENROLMENT_TOKEN}`);
    await fillPasswords(user, GOOD_PASSWORD);
    await user.click(screen.getByRole("button", { name: "Finish set-up" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Choose a passkey or an authenticator app.");
    expect(await api.getSession()).toBeNull();
  });

  it("refuses a short password, passwords that differ, and a leaked password", async () => {
    const { api, user } = renderEnrol(`#${STUB_ENROLMENT_TOKEN}`);
    await fillPasswords(user, "short");
    await user.click(screen.getByRole("radio", { name: /Passkey/ }));
    await user.click(screen.getByRole("button", { name: "Create passkey and finish" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Use at least 10 characters");

    await user.clear(screen.getByLabelText("Password"));
    await user.clear(screen.getByLabelText("Type it again"));
    await fillPasswords(user, GOOD_PASSWORD, "a different sentence");
    await user.click(screen.getByRole("button", { name: "Create passkey and finish" }));
    expect(screen.getByRole("alert")).toHaveTextContent("The two passwords do not match.");

    await user.clear(screen.getByLabelText("Password"));
    await user.clear(screen.getByLabelText("Type it again"));
    await fillPasswords(user, STUB_LEAKED_PASSWORD);
    await user.click(screen.getByRole("button", { name: "Create passkey and finish" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("appeared in a known leak");
    expect(await api.getSession()).toBeNull();
  });

  it("refuses a wrong first code and stays on the page", async () => {
    const { api, user } = renderEnrol(`#${STUB_ENROLMENT_TOKEN}`);
    await fillPasswords(user, GOOD_PASSWORD);
    await user.click(screen.getByRole("radio", { name: /Authenticator app/ }));
    await user.type(await screen.findByLabelText("6-digit code"), "000000");
    await user.click(screen.getByRole("button", { name: "Finish set-up" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("That code is not right.");
    expect(await api.getSession()).toBeNull();
  });

  it("offers only the second steps the service allows", async () => {
    const stub = createStubAuthApi();
    const api: AuthApi = {
      ...stub,
      getEnrolment: async (token) => ({ ...(await stub.getEnrolment(token)), methods: ["authenticator"] }),
    };
    renderEnrol(`#${STUB_ENROLMENT_TOKEN}`, api);

    expect(await screen.findByRole("radio", { name: /Authenticator app/ })).toBeChecked();
    expect(screen.queryByRole("radio", { name: /Passkey/ })).not.toBeInTheDocument();
    expect(await screen.findByTitle("QR code for your authenticator app")).toBeInTheDocument();
  });
});
