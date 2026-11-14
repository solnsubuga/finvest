import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { createStubDirectory } from "@/directory/stub.ts";
import { CorrectionsError } from "../investments/corrections/api.ts";
import { createStubCorrectionsApi } from "../investments/corrections/stub.ts";
import {
  createStubHeldActionsApi,
  MessageError,
  type HeldActionsApi,
} from "./actions.ts";
import { FIRST_MESSAGE } from "./HeldActions.tsx";
import { MaturitiesScreen, type HeldTools } from "./index.tsx";
import { createStubMaturitiesApi } from "./stub.ts";

const ME = "sample-me";

function renderHeld(tools: Partial<HeldTools> = {}) {
  let n = 0;
  const api =
    tools.api ??
    createStubHeldActionsApi(
      createStubCorrectionsApi(ME, "default", () => "2026-11-14T09:00:00Z"),
    );
  render(
    <MemoryRouter>
      <MaturitiesScreen
        api={createStubMaturitiesApi("sample", 0)}
        directory={createStubDirectory()}
        held={{
          api,
          canSend: true,
          canMessage: true,
          newKey: () => `key-${++n}`,
          ...tools,
        }}
      />
    </MemoryRouter>,
  );
  return { api, user: userEvent.setup() };
}

const heldTable = () =>
  screen.findByRole("table", {
    name: "Matured terms whose money waits for the client",
  });

describe("maturities: what staff do about held money", () => {
  it("proposes sending all of it to another of the client's accounts, for someone else to approve", async () => {
    const { user } = renderHeld();
    await user.click(
      await screen.findByRole("button", {
        name: "Send Joan Namutebi's money to another account",
      }),
    );
    const form = screen.getByRole("form", { name: "Send to another account" });
    expect(form).toHaveTextContent("UGX 1,026,490");
    await user.click(
      await within(form).findByRole("radio", {
        name: /Airtel Money •••• 4471/,
      }),
    );
    await user.click(
      within(form).getByRole("button", { name: "Propose sending it" }),
    );
    expect(form).toHaveTextContent("Fill this in.");
    await user.type(
      within(form).getByLabelText(/Why\?/),
      "The client called at 10:05 and asked for their Airtel number.",
    );
    await user.click(
      within(form).getByRole("button", { name: "Propose sending it" }),
    );
    const table = await heldTable();
    await waitFor(() =>
      expect(table).toHaveTextContent("waits for a second person"),
    );
    expect(
      within(table).queryByRole("button", { name: /to another account/ }),
    ).toBeNull();
  });

  it("shows the backend's refusal of the chosen account beside it", async () => {
    const base = createStubHeldActionsApi(createStubCorrectionsApi(ME));
    const proposePayOut = vi
      .fn<HeldActionsApi["proposePayOut"]>()
      .mockRejectedValue(
        new CorrectionsError("invalid", [
          { field: "destination_id", code: "destination_in_safety_wait" },
        ]),
      );
    const { user } = renderHeld({ api: { ...base, proposePayOut } });
    await user.click(
      await screen.findByRole("button", { name: /to another account/ }),
    );
    const form = screen.getByRole("form", { name: "Send to another account" });
    await user.click(await within(form).findByRole("radio", { name: /Bank/ }));
    await user.type(within(form).getByLabelText(/Why\?/), "Asked in chat.");
    await user.click(
      within(form).getByRole("button", { name: "Propose sending it" }),
    );
    expect(
      await within(form).findByText(
        "That payout account is still in its first day's safety wait.",
      ),
    ).toBeInTheDocument();
    expect(proposePayOut).toHaveBeenCalledWith(
      "key-1",
      "-InvSample00000000H1",
      "-DestSample000000012",
      "Asked in chat.",
    );
  });

  it("says when the client has no approved account to send it to", async () => {
    const base = createStubHeldActionsApi(createStubCorrectionsApi(ME));
    const { user } = renderHeld({
      api: { ...base, payoutAccounts: () => Promise.resolve([]) },
    });
    await user.click(
      await screen.findByRole("button", { name: /to another account/ }),
    );
    const form = screen.getByRole("form", { name: "Send to another account" });
    expect(
      await within(form).findByText(/has no approved payout account/),
    ).toBeInTheDocument();
    expect(
      within(form).getByRole("button", { name: "Propose sending it" }),
    ).toBeDisabled();
  });

  it("writes to the client in their help chat, starting from the usual words", async () => {
    const base = createStubHeldActionsApi(createStubCorrectionsApi(ME));
    const message = vi.fn<HeldActionsApi["message"]>().mockResolvedValue();
    const { user } = renderHeld({ api: { ...base, message } });
    await user.click(
      await screen.findByRole("button", { name: "Message Joan Namutebi" }),
    );
    const form = screen.getByRole("form", { name: "Message the client" });
    expect(within(form).getByLabelText(/Message/)).toHaveValue(FIRST_MESSAGE);
    await user.click(
      within(form).getByRole("button", { name: "Send message" }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "they are told there is a message",
    );
    expect(message).toHaveBeenCalledWith(
      "key-1",
      "sample-client-joan",
      FIRST_MESSAGE,
    );
  });

  it("says when the client can no longer be written to", async () => {
    const base = createStubHeldActionsApi(createStubCorrectionsApi(ME));
    const { user } = renderHeld({
      api: {
        ...base,
        message: () => Promise.reject(new MessageError("not_found")),
      },
    });
    await user.click(await screen.findByRole("button", { name: /^Message / }));
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "locked or closed",
    );
  });

  it("shows each action only to those whose role includes it", async () => {
    renderHeld({ canSend: false });
    await screen.findByRole("button", { name: /^Message / });
    expect(
      screen.queryByRole("button", { name: /to another account/ }),
    ).toBeNull();
  });
});
