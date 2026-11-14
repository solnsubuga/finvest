import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { KycError, notConnectedKycApi, type KycApi } from "./api.ts";
import { IdentityChecksScreen } from "./index.tsx";
import { maskDocumentNumber, timeAgo } from "./labels.ts";
import { createStubKycApi } from "./stub.ts";

const DAVID = "-OdSample0000000001A";

function renderScreen(api: KycApi = createStubKycApi(0), entry = "/identity-checks", canDecide = true) {
  const element = <IdentityChecksScreen api={api} canDecide={canDecide} />;
  const router = createMemoryRouter([{ path: "/identity-checks", element }], { initialEntries: [entry] });
  render(<RouterProvider router={router} />);
  return { api, router, user: userEvent.setup() };
}

async function openCheck(name: string) {
  return screen.findByRole("article", { name: `Identity check for ${name}` });
}

describe("identity checks screen", () => {
  it("lists the queue and opens the first check that needs a person", async () => {
    renderScreen();
    const review = await openCheck("David Mugisha");
    expect(screen.getByText("2 clients are waiting for a decision.")).toBeInTheDocument();
    const queue = screen.getByRole("list", { name: "Identity checks" });
    expect(within(queue).getAllByRole("button").map((button) => button.textContent)).toEqual([
      expect.stringContaining("David Mugisha"),
      expect.stringContaining("Ruth Akello"),
      expect.stringContaining("Grace Namara"),
      expect.stringContaining("Joseph Nakato"),
    ]);
    expect(within(review).getByText("Selfie could not be confirmed")).toBeInTheDocument();
    expect(within(review).getByText("Face match 62%")).toBeInTheDocument();
    expect(within(review).getByText("MUGISHA David")).toBeInTheDocument();
    expect(within(review).getByText("14 Mar 1990")).toBeInTheDocument();
  });

  it("hides the ID number until asked", async () => {
    const { user } = renderScreen();
    const review = await openCheck("David Mugisha");
    expect(within(review).getByText("CM90••••••••4PL")).toBeInTheDocument();
    await user.click(within(review).getByRole("button", { name: "Show" }));
    expect(await within(review).findByText("CM90123456784PL")).toBeInTheDocument();
  });

  it("says what the client has not given yet, and asks for the number only once shown", async () => {
    const stub = createStubKycApi(0);
    const api: KycApi = {
      ...stub,
      getCheck: async (id) => ({
        ...(await stub.getCheck(id)),
        name: null,
        surname: null,
        givenNames: null,
        documentNumberMasked: null,
        dateOfBirth: null,
        documentExpiresOn: null,
      }),
      showDocumentNumber: vi.fn(stub.showDocumentNumber),
    };
    renderScreen(api);
    const review = await openCheck("a client");
    expect(within(review).getByRole("heading", { name: "Details not confirmed yet" })).toBeInTheDocument();
    expect(within(review).getAllByText("Not given yet")).toHaveLength(4);
    expect(within(review).queryByRole("button", { name: "Show" })).toBeNull();
    expect(api.showDocumentNumber).not.toHaveBeenCalled();
  });

  it("shows the name the network holds for the client's phone beside the other checks", async () => {
    const { user } = renderScreen();
    const first = await openCheck("David Mugisha");
    const matches = within(first).getByText("Phone registered to this name").closest("li");
    expect(matches).toHaveTextContent("Matches");
    expect(matches).not.toHaveTextContent("David Mugisha");
    await user.click(screen.getByRole("button", { name: /Grace Namara/ }));
    const third = await openCheck("Grace Namara");
    expect(within(third).getByText("Phone registered to this name").closest("li")).toHaveTextContent(
      "Different name: Peter Okot",
    );
  });

  it("opens another check from the queue and says when no automatic checks ran", async () => {
    const { user, router } = renderScreen();
    await openCheck("David Mugisha");
    await user.click(screen.getByRole("button", { name: /Ruth Akello/ }));
    const review = await openCheck("Ruth Akello");
    expect(router.state.location.search).toBe("?check=-OdSample0000000002A");
    expect(within(review).getByText(/No automatic checks ran/)).toBeInTheDocument();
    expect(within(review).getByRole("checkbox", { name: "Passport, photo page" })).toBeInTheDocument();
    expect(within(review).queryByRole("checkbox", { name: /back/ })).not.toBeInTheDocument();
  });

  it("needs a reason, asks once more, then approves and shows the account number", async () => {
    const { user, api } = renderScreen();
    const approve = vi.spyOn(api, "approve");
    const review = await openCheck("David Mugisha");
    await user.click(within(review).getByRole("button", { name: "Approve" }));
    expect(within(review).getByText("Give the reason for your decision.")).toBeInTheDocument();
    expect(approve).not.toHaveBeenCalled();

    await user.type(within(review).getByLabelText("Reason for your decision"), "  Photos match the ID ");
    await user.click(within(review).getByRole("button", { name: "Approve" }));
    expect(within(review).getByRole("alertdialog")).toHaveTextContent("It cannot be undone");
    expect(approve).not.toHaveBeenCalled();
    await user.click(within(review).getByRole("button", { name: "Yes, approve" }));

    expect(await within(review).findByText(/Account number/)).toHaveTextContent(/Account number 10\d\d \d{3} \d{3} was issued/);
    expect(approve).toHaveBeenCalledWith(DAVID, "Photos match the ID");
    expect(within(review).queryByRole("form", { name: "Decision" })).not.toBeInTheDocument();
    expect(screen.getByText("One client is waiting for a decision.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /David Mugisha/ })).not.toBeInTheDocument();
  });

  it("keeps the approval on screen when it was the last check in the queue", async () => {
    const stub = createStubKycApi(0);
    const api: KycApi = { ...stub, listQueue: async () => (await stub.listQueue()).filter((item) => item.id === DAVID) };
    const { user } = renderScreen(api);
    const review = await openCheck("David Mugisha");
    await user.type(within(review).getByLabelText("Reason for your decision"), "Photos match the ID");
    await user.click(within(review).getByRole("button", { name: "Approve" }));
    await user.click(within(review).getByRole("button", { name: "Yes, approve" }));

    expect(await within(review).findByText(/Account number/)).toHaveTextContent(/was issued/);
    expect(await screen.findByText("Nobody is waiting for a decision.")).toBeInTheDocument();
    expect(screen.queryByText("Nothing to review")).not.toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Identity check for David Mugisha" })).toBeInTheDocument();
  });

  it("can back out of the confirmation", async () => {
    const { user, api } = renderScreen();
    const reject = vi.spyOn(api, "reject");
    const review = await openCheck("David Mugisha");
    await user.type(within(review).getByLabelText("Reason for your decision"), "Forged");
    await user.click(within(review).getByRole("button", { name: "Reject" }));
    await user.click(within(review).getByRole("button", { name: "Go back" }));
    expect(reject).not.toHaveBeenCalled();
    expect(within(review).getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("asks for named photos with a message to the client", async () => {
    const { user, api } = renderScreen();
    const ask = vi.spyOn(api, "askForPhotos");
    const review = await openCheck("David Mugisha");
    await user.click(within(review).getByRole("button", { name: "Ask for new photos" }));
    expect(within(review).getByText("Choose at least one photo for the client to take again.")).toBeInTheDocument();
    expect(within(review).getByText("Tell the client what to fix.")).toBeInTheDocument();

    await user.click(within(review).getByRole("checkbox", { name: "Selfie" }));
    await user.type(within(review).getByLabelText("Message to the client"), "Your selfie was too dark.");
    await user.click(within(review).getByRole("button", { name: "Ask for new photos" }));

    expect(await within(review).findByRole("status")).toHaveTextContent(
      "Waiting for the client to retake: selfie. They were told: “Your selfie was too dark.”",
    );
    expect(ask).toHaveBeenCalledWith(DAVID, ["selfie"], "Your selfie was too dark.");
    expect(within(review).getByText("New photo asked for")).toBeInTheDocument();
  });

  it("escalates to compliance, who cannot escalate again", async () => {
    const { user } = renderScreen();
    const review = await openCheck("David Mugisha");
    await user.type(within(review).getByLabelText("Reason for your decision"), "Name on a watch list");
    await user.click(within(review).getByRole("button", { name: "Escalate to compliance" }));
    expect(await within(review).findByText("With compliance")).toBeInTheDocument();
    expect(within(review).queryByRole("button", { name: "Escalate to compliance" })).not.toBeInTheDocument();
    expect(within(review).getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("shows a check that is waiting for the client without any decision buttons", async () => {
    renderScreen(undefined, "/identity-checks?check=-OdSample0000000004A");
    const review = await openCheck("Joseph Nakato");
    expect(within(review).getByRole("status")).toHaveTextContent("Waiting for the client to retake: front of the id.");
    expect(within(review).queryByRole("form", { name: "Decision" })).not.toBeInTheDocument();
  });

  it("reloads the check when someone else decided it first", async () => {
    const api = createStubKycApi(0);
    const { user } = renderScreen(api);
    const review = await openCheck("David Mugisha");
    await api.reject(DAVID, "Forged", "");
    await user.type(within(review).getByLabelText("Reason for your decision"), "Looks fine");
    await user.click(within(review).getByRole("button", { name: "Escalate to compliance" }));
    expect(await within(review).findByText("Rejected. This decision is final.")).toBeInTheDocument();
  });

  it("keeps the form and says why when a decision fails", async () => {
    const api = createStubKycApi(0);
    vi.spyOn(api, "escalate").mockRejectedValue(new KycError("unavailable"));
    const { user } = renderScreen(api);
    const review = await openCheck("David Mugisha");
    await user.type(within(review).getByLabelText("Reason for your decision"), "Unsure");
    await user.click(within(review).getByRole("button", { name: "Escalate to compliance" }));
    expect(await within(review).findByRole("alert")).toHaveTextContent("We could not reach Finvest");
    expect(within(review).getByLabelText("Reason for your decision")).toHaveValue("Unsure");
  });

  it("says so when there is nothing to review", async () => {
    renderScreen({ ...createStubKycApi(0), listQueue: () => Promise.resolve([]) });
    expect(await screen.findByRole("heading", { name: "Nothing to review" })).toBeInTheDocument();
    expect(screen.getByText("Nobody is waiting for a decision.")).toBeInTheDocument();
  });

  it("offers to try again when the queue cannot be loaded", async () => {
    const api = createStubKycApi(0);
    const list = vi.spyOn(api, "listQueue").mockRejectedValueOnce(new KycError("unavailable"));
    const { user } = renderScreen(api);
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not reach Finvest");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await openCheck("David Mugisha");
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("lets someone without the review permission look but not decide", async () => {
    renderScreen(undefined, "/identity-checks", false);
    const review = await openCheck("David Mugisha");
    expect(within(review).queryByRole("form", { name: "Decision" })).not.toBeInTheDocument();
    expect(within(review).getByText(/Deciding it needs the identity check review permission/)).toBeInTheDocument();
    expect(within(review).getByRole("button", { name: "Show" })).toBeInTheDocument();
  });

  it("moves focus to the confirmation before approving", async () => {
    const { user } = renderScreen();
    const review = await openCheck("David Mugisha");
    await user.type(within(review).getByLabelText("Reason for your decision"), "Photos match");
    await user.click(within(review).getByRole("button", { name: "Approve" }));
    expect(within(review).getByRole("button", { name: "Yes, approve" })).toHaveFocus();
  });

  it("offers to try again when one check cannot be loaded", async () => {
    const api = createStubKycApi(0);
    const get = vi.spyOn(api, "getCheck").mockRejectedValueOnce(new KycError("unavailable"));
    const { user } = renderScreen(api);
    expect(await screen.findByRole("heading", { name: "This check could not be loaded" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await openCheck("David Mugisha");
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("names the provider and expiry flags, and opens photos without a referrer", async () => {
    const stub = createStubKycApi(0);
    for (const [flag, words] of [
      ["automatic_checks_passed", "Automatic checks passed"],
      ["document_expired", "ID has expired"],
      ["provider_not_received", "Provider never received the check"],
      ["provider_refused", "Provider refused the check"],
      ["provider_no_answer", "Provider did not answer"],
      ["phone_name_not_confirmed", "Phone name not confirmed"],
    ] as const) {
      const api: KycApi = {
        ...stub,
        getCheck: async (id) => ({
          ...(await stub.getCheck(id)),
          reviewFlags: [flag],
          photos: [{ kind: "selfie", url: "https://files.test/selfie", live: false }],
        }),
      };
      const router = createMemoryRouter([{ path: "/identity-checks", element: <IdentityChecksScreen api={api} /> }], {
        initialEntries: ["/identity-checks"],
      });
      const { unmount } = render(<RouterProvider router={router} />);
      const review = await openCheck("David Mugisha");
      expect(within(review).getByText(words)).toBeInTheDocument();
      expect(within(review).getByRole("img", { name: "Selfie" })).toHaveAttribute("referrerpolicy", "no-referrer");
      unmount();
    }
  });

  it("shows which selfie was taken live", async () => {
    renderScreen();
    const review = await openCheck("David Mugisha");
    expect(within(review).getByText("Taken live")).toBeInTheDocument();
    expect(within(review).getByText("Selfie taken live")).toBeInTheDocument();
    expect(within(review).getByText("Live")).toBeInTheDocument();
  });

  it("names the liveness flags", async () => {
    const stub = createStubKycApi(0);
    for (const [flag, words] of [
      ["liveness_not_run", "Selfie not taken live"],
      ["liveness_fail", "Live check failed, camera selfie sent"],
    ] as const) {
      const api: KycApi = {
        ...stub,
        getCheck: async (id) => ({ ...(await stub.getCheck(id)), reviewFlags: [flag] }),
      };
      const router = createMemoryRouter([{ path: "/identity-checks", element: <IdentityChecksScreen api={api} /> }], {
        initialEntries: ["/identity-checks"],
      });
      const { unmount } = render(<RouterProvider router={router} />);
      expect(within(await openCheck("David Mugisha")).getByText(words)).toBeInTheDocument();
      unmount();
    }
  });

  it("says it is not connected in a build with no API client", async () => {
    renderScreen(notConnectedKycApi);
    expect(await screen.findByRole("alert")).toHaveTextContent("not connected to the Finvest service yet");
  });
});

describe("labels", () => {
  it("masks the middle of a document number", () => {
    expect(maskDocumentNumber("CM90123456784PL")).toBe("CM90••••••••4PL");
    expect(maskDocumentNumber("A00123")).toBe("••••••");
  });

  it("words how long ago a check was submitted", () => {
    const now = new Date("2026-11-13T12:00:00Z");
    expect(timeAgo("2026-11-13T11:59:40Z", now)).toBe("just now");
    expect(timeAgo("2026-11-13T11:55:00Z", now)).toBe("5 min ago");
    expect(timeAgo("2026-11-13T10:00:00Z", now)).toBe("2 h ago");
    expect(timeAgo("2026-11-12T10:00:00Z", now)).toBe("1 day ago");
    expect(timeAgo("2026-11-10T10:00:00Z", now)).toBe("3 days ago");
  });
});
