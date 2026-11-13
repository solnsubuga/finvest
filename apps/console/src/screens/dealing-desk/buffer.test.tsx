import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import type { Directory } from "@/directory/api.ts";
import { emptyDirectory } from "@/directory/api.ts";
import { BufferSection } from "./Buffer.tsx";
import type { Currency } from "./api.ts";
import { BufferError, notConnectedBufferApi, type BufferApi, type Movement } from "./buffer.ts";
import { createHttpBufferApi, toBufferError, type BufferScreenJson } from "./bufferHttp.ts";
import { majorText } from "./format.ts";
import { BUFFER_COLLEAGUE, createStubBufferApi } from "./bufferStub.ts";

const ME = "-Nstaffme00000000000";
const TODAY = "2026-11-13";
const NOW = () => new Date("2026-11-13T10:00:00Z");
const names: Directory = { ...emptyDirectory, staffName: (id) => Promise.resolve(id === BUFFER_COLLEAGUE.id ? BUFFER_COLLEAGUE.name : null) };

function renderBuffer(api: BufferApi = createStubBufferApi({ id: ME }, "sample", NOW), canChange = true) {
  let keys = 0;
  render(
    <BufferSection api={api} directory={names} me={ME} canChange={canChange} newKey={() => `key-${++keys}`} today={() => TODAY} />,
  );
  return { api, user: userEvent.setup() };
}

const money = () => screen.getByRole("region", { name: "Finvest's money in the buffer" });
const movements = () => screen.findByRole("list", { name: "Money paid in and taken out" });
const item = async (text: string) => (await screen.findByText(text)).closest("li") as HTMLElement;

describe("liquidity buffer", () => {
  it("shows Finvest's money, the rules in force and what waits for a second person", async () => {
    renderBuffer();
    await movements();
    expect(within(money()).getByText("UGX 120,000,000")).toBeInTheDocument();
    expect(screen.getByText(/Buffer after promised withdrawals UGX 118.5m/)).toBeInTheDocument();
    const waiting = await item("Paid in UGX 40,000,000");
    expect(within(waiting).getByText("Waiting for a second person")).toBeInTheDocument();
    expect(await within(waiting).findByText("Daniel Okello, 13 Nov 2026, 11:40")).toBeInTheDocument();
    expect(within(waiting).getByText("FT26317KX2Q4")).toBeInTheDocument();
    const rules = screen.getByRole("region", { name: "Buffer rules" });
    expect(within(rules).getByText("UGX 150,000,000")).toBeInTheDocument();
    expect(within(rules).getByText("New rules from 1 Dec 2026")).toBeInTheDocument();
  });

  it("lets a second person approve money paid in after checking the statement", async () => {
    const { user } = renderBuffer();
    const waiting = await item("Paid in UGX 40,000,000");
    await user.click(within(waiting).getByRole("button", { name: "Approve as second person" }));
    expect(within(waiting).getByText(/statement show UGX 40,000,000 arriving with reference FT26317KX2Q4/)).toBeInTheDocument();
    await user.click(within(waiting).getByRole("button", { name: "Yes, approve and post" }));
    expect(await screen.findByText("UGX 160,000,000")).toBeInTheDocument();
    expect(within(await item("Paid in UGX 40,000,000")).getByText("Approved and posted")).toBeInTheDocument();
  });

  it("records money paid in for someone else to approve, and offers its recorder nothing", async () => {
    const { user } = renderBuffer();
    await movements();
    await user.click(screen.getByRole("button", { name: "Record money paid in" }));
    const form = screen.getByRole("form", { name: "Record money paid in" });
    await user.click(within(form).getByRole("button", { name: "Record for approval" }));
    expect(within(form).getByText("Enter a whole amount in UGX.")).toBeInTheDocument();
    expect(within(form).getByText("Enter the bank's reference for the transfer.")).toBeInTheDocument();
    await user.type(within(form).getByLabelText("Amount (UGX)"), "25,000,000");
    await user.type(within(form).getByLabelText("Bank reference"), "FT26317ZZ");
    await user.type(within(form).getByLabelText("Reason"), "Third tranche");
    await user.click(within(form).getByRole("button", { name: "Record for approval" }));
    const recorded = await item("Paid in UGX 25,000,000");
    expect(within(recorded).getByText("You recorded this, so someone else has to approve it.")).toBeInTheDocument();
    expect(within(recorded).queryByRole("button")).toBeNull();
  });

  it("explains why taking out more than the buffer holds is refused", async () => {
    const { user } = renderBuffer();
    await movements();
    await user.click(screen.getByRole("button", { name: "Take money out" }));
    const form = screen.getByRole("form", { name: "Take money out" });
    await user.type(within(form).getByLabelText("Amount (UGX)"), "119,000,000");
    await user.type(within(form).getByLabelText("Reason"), "Back to operating account");
    await user.click(within(form).getByRole("button", { name: "Propose for approval" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("That is more than the buffer holds now. Client money never leaves with it.");
  });

  it("sends back a rules change with a note", async () => {
    const { user } = renderBuffer();
    const change = await item("New rules from 1 Dec 2026");
    await user.click(within(change).getByRole("button", { name: "Send back" }));
    await user.type(within(change).getByLabelText("Why are you sending it back?"), "Keep the floor at 100m");
    await user.click(within(change).getByRole("button", { name: "Send back" }));
    const sent = await item("New rules from 1 Dec 2026");
    expect(within(sent).getByText("Keep the floor at 100m")).toBeInTheDocument();
    expect(within(sent).getAllByText("Sent back").length).toBeGreaterThan(0);
  });

  it("proposes new rules from a later day only", async () => {
    const { user } = renderBuffer();
    await movements();
    await user.click(screen.getByRole("button", { name: "Propose new rules" }));
    const form = screen.getByRole("form", { name: "Propose new rules" });
    await user.type(within(form).getByLabelText("Reason"), "Holidays");
    await user.click(within(form).getByRole("button", { name: "Propose for approval" }));
    expect(within(form).getByText("New rules start on a later day than today.")).toBeInTheDocument();
    await user.type(within(form).getByLabelText("Starts on"), "2027-01-04");
    await user.click(within(form).getByRole("button", { name: "Propose for approval" }));
    const proposed = await item("New rules from 4 Jan 2027");
    expect(within(proposed).getByRole("button", { name: "Withdraw" })).toBeInTheDocument();
  });

  it("asks for the transfer out only on the day it was approved", async () => {
    const stub = createStubBufferApi({ id: ME }, "sample", NOW);
    const out = (id: string, valueDate: string): Movement => ({
      id,
      direction: "out",
      amount: { minor: 5_000_000, currency: "UGX" },
      bankReference: null,
      valueDate,
      reason: `Back to operating account ${id}`,
      status: "approved",
      proposedBy: BUFFER_COLLEAGUE.id,
      proposedAt: `${valueDate}T07:00:00Z`,
      decidedBy: ME,
      decidedAt: `${valueDate}T08:00:00Z`,
      decisionReason: null,
      allowed: [],
    });
    const getBuffer = async (currency: Currency) => {
      const screen = await stub.getBuffer(currency);
      return { ...screen, movements: [out("-Nmoveout00000today", TODAY), out("-Nmoveout000earlier", "2026-10-02"), ...screen.movements] };
    };
    renderBuffer({ ...stub, getBuffer });
    const todays = (await screen.findByText("Back to operating account -Nmoveout00000today")).closest("li") as HTMLElement;
    const earlier = (await screen.findByText("Back to operating account -Nmoveout000earlier")).closest("li") as HTMLElement;
    expect(within(todays).getByText(/Make the transfer from the trust account today/)).toBeInTheDocument();
    expect(within(earlier).queryByText(/Make the transfer/)).toBeNull();
    expect(await within(earlier).findByText("Daniel Okello, 2 Oct 2026, 10:00")).toBeInTheDocument();
  });

  it("shows a reader the buffer with nothing to change", async () => {
    renderBuffer(undefined, false);
    await movements();
    expect(screen.queryByRole("button", { name: "Record money paid in" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Propose new rules" })).toBeNull();
  });

  it("says so before Finvest has paid any money in", async () => {
    renderBuffer(createStubBufferApi({ id: ME }, "empty", NOW));
    expect(await screen.findByText(/Finvest has not paid any money into the buffer yet/)).toBeInTheDocument();
    expect(screen.getByText("UGX 0")).toBeInTheDocument();
  });

  it("offers to try again when the buffer cannot be loaded", async () => {
    const getBuffer = vi.fn(notConnectedBufferApi.getBuffer).mockRejectedValueOnce(new BufferError("unavailable"));
    const { user } = renderBuffer({ ...createStubBufferApi({ id: ME }, "sample", NOW), getBuffer });
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not reach Finvest.");
    getBuffer.mockImplementation(createStubBufferApi({ id: ME }, "sample", NOW).getBuffer);
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(within(money()).getByText("UGX 120,000,000")).toBeInTheDocument());
  });
});

describe("starting figures in the rules form", () => {
  it("are whole major units, or empty when the amount has a fraction", () => {
    expect(majorText(150_000_000, "UGX")).toBe("150000000");
    expect(majorText(500_000, "USD")).toBe("5000");
    expect(majorText(500_050, "USD")).toBe("");
  });
});

describe("liquidity buffer over the API", () => {
  const json: BufferScreenJson = {
    currency: "UGX",
    buffer: { minor: 5, currency: "UGX" },
    level: "below_floor",
    in_force: {
      effective_from: "2026-01-01",
      target: { minor: 3, currency: "UGX" },
      floor: { minor: 2, currency: "UGX" },
      from_buffer_up_to: { minor: 1, currency: "UGX" },
      pay_within_working_days: 2,
    },
    versions: [],
    proposals: [],
    finvest_money: { minor: 7, currency: "UGX" },
    movements: [],
  };

  it("sends a pay-in with its key and reads the screen back", async () => {
    const request = vi.fn().mockResolvedValue(json);
    const api = createHttpBufferApi({ request, download: vi.fn() } as unknown as Http);
    const answer = await api.proposeMovement("k-1", {
      direction: "in",
      amount: { minor: 7, currency: "UGX" },
      reason: "Top up",
      bankReference: "FT1",
      valueDate: TODAY,
    });
    expect(request).toHaveBeenCalledWith(
      "POST",
      "/staff/dealing/buffer/capital",
      { direction: "in", currency: "UGX", amount_minor: 7, reason: "Top up", bank_reference: "FT1", value_date: TODAY },
      { idempotencyKey: "k-1" },
    );
    expect(answer.finvestMoney).toEqual({ minor: 7, currency: "UGX" });
    expect(answer.inForce.fromBufferUpTo).toEqual({ minor: 1, currency: "UGX" });
  });

  it("reads the backend's refusals as the screen's own", () => {
    const field = (code: string) => new ApiError("validation_failed", 422, [{ field_path: ["amount"], code }]);
    expect(toBufferError(field("more_than_the_buffer")).code).toBe("more_than_the_buffer");
    expect(toBufferError(field("day_closed_decline_and_record_again")).code).toBe("day_closed_decline_and_record_again");
    expect(toBufferError(field("inconsistent")).code).toBe("rules_inconsistent");
    expect(toBufferError(field("something_else")).code).toBe("invalid");
    expect(toBufferError(new ApiError("forbidden", 403)).code).toBe("forbidden");
    expect(toBufferError(new TypeError("x")).code).toBe("unavailable");
  });
});
