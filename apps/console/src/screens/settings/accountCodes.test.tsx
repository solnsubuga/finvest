import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import { AccountCodesSection } from "./AccountCodes.tsx";
import { AccountCodesError, createHttpAccountCodesApi, notConnectedAccountCodesApi, toAccountCodesError } from "./accountCodes.ts";
import { createStubAccountCodesApi } from "./accountCodesStub.ts";

const ME = { id: "sample-me", name: "Amina" };
const NOW = () => Date.parse("2026-11-14T09:00:00Z");

function renderCodes(canChange = true, sample: "sample" | "empty" = "sample") {
  const api = createStubAccountCodesApi(ME, NOW, sample);
  render(<AccountCodesSection api={api} canChange={canChange} />);
  return { api, user: userEvent.setup() };
}

describe("accounting system codes", () => {
  it("opens on the change waiting for a second person, and approves it with a note", async () => {
    const { user } = renderCodes();
    const panel = await screen.findByRole("tabpanel", { name: "Xero" });
    expect(screen.getByRole("tab", { name: /Xero/ })).toHaveAttribute("aria-selected", "true");
    expect(within(panel).getByText(/Proposed by Grace Atim, .* 2 accounts change\./)).toBeInTheDocument();
    const iotec = within(panel).getByRole("rowheader", { name: /ioTec fees/ }).closest("tr") as HTMLElement;
    expect(iotec).toHaveTextContent("404");
    expect(iotec).toHaveTextContent("No code");

    await user.click(within(panel).getByRole("button", { name: "Approve the codes" }));
    expect(within(panel).getByText("Write a note first. It goes on the audit trail.")).toBeInTheDocument();
    await user.type(within(panel).getByRole("textbox", { name: /Note for the audit trail/ }), "Matches the chart");
    await user.click(within(panel).getByRole("button", { name: "Approve the codes" }));
    await waitFor(() => expect(within(screen.getByRole("tabpanel", { name: "Xero" })).getByText(/approved by Amina/)).toBeInTheDocument());
    expect(screen.getByRole("tab", { name: /Xero/ })).toHaveTextContent("In use");
  });

  it("sets up codes for a system, checks them and sends them for approval", async () => {
    const { user } = renderCodes();
    await screen.findByRole("tabpanel", { name: "Xero" });
    await user.click(screen.getByRole("tab", { name: /Sage Accounting/ }));
    const panel = screen.getByRole("tabpanel", { name: "Sage Accounting" });
    expect(within(panel).getByText(/No codes approved yet/)).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Set up codes" }));
    await user.type(within(panel).getByRole("textbox", { name: "Nominal code for Trust bank account" }), "12A");
    expect(within(panel).getByText("A Sage nominal code is a number of up to 8 digits.")).toBeInTheDocument();
    await user.clear(within(panel).getByRole("textbox", { name: "Nominal code for Trust bank account" }));
    await user.type(within(panel).getByRole("textbox", { name: "Nominal code for Trust bank account" }), "1210");
    expect(within(panel).queryByRole("textbox", { name: /Tax rate/ })).not.toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Send for approval" }));
    expect(within(panel).getByText("Say why, so the approver knows what to check.")).toBeInTheDocument();
    await user.type(within(panel).getByRole("textbox", { name: /Why change them/ }), "From the accountant");
    await user.click(within(panel).getByRole("button", { name: "Send for approval" }));
    const waiting = await screen.findByRole("tabpanel", { name: "Sage Accounting" });
    expect(within(waiting).getByText(/Proposed by you/)).toBeInTheDocument();
    // The proposer can only withdraw.
    expect(within(waiting).queryByRole("button", { name: "Approve the codes" })).not.toBeInTheDocument();
    await user.type(within(waiting).getByRole("textbox", { name: /Why withdraw it/ }), "Wrong chart");
    await user.click(within(waiting).getByRole("button", { name: "Withdraw it" }));
    await waitFor(() => expect(screen.getByRole("tab", { name: /Sage Accounting/ })).toHaveTextContent("Not set up"));
    expect(within(screen.getByRole("tabpanel", { name: "Sage Accounting" })).getByText(/Withdrawn .* proposed by you\. From the accountant/)).toBeInTheDocument();
  });

  it("asks Xero for a tax rate, starting from the no-tax rate", async () => {
    const { user } = renderCodes(true, "empty");
    await screen.findByRole("tabpanel", { name: "QuickBooks Online" });
    await user.click(screen.getByRole("tab", { name: /Xero/ }));
    await user.click(screen.getByRole("button", { name: "Set up codes" }));
    expect(screen.getByRole("textbox", { name: /Tax rate on every line/ })).toHaveValue("Tax Exempt");
  });

  it("only shows the codes to someone who cannot change them", async () => {
    renderCodes(false);
    const panel = await screen.findByRole("tabpanel", { name: "Xero" });
    expect(within(panel).getByText("Someone who can change accounting system codes decides it.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Change codes|Set up codes|Approve/ })).not.toBeInTheDocument();
  });

  it("offers to try again when the codes cannot be loaded", async () => {
    render(<AccountCodesSection api={notConnectedAccountCodesApi} canChange />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Accounting system codes are not connected to the Finvest service yet.");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("accounting system codes over HTTP", () => {
  it("sends a whole map with its reason, and a decision with its note", async () => {
    const empty = { codes: [], systems: [] };
    const request = vi.fn().mockResolvedValue(empty);
    const api = createHttpAccountCodesApi({ request, download: vi.fn() } as unknown as Http);
    await api.list();
    expect(request).toHaveBeenLastCalledWith("GET", "/staff/finance/account-codes");
    await api.propose("xero", { accounts: { "bank.trust": "090" }, taxRate: "Tax Exempt", reason: "First" });
    expect(request).toHaveBeenLastCalledWith("POST", "/staff/finance/account-codes/xero/proposals", {
      accounts: { "bank.trust": "090" },
      tax_rate: "Tax Exempt",
      reason: "First",
    });
    await api.decide("map-1", "approved", "Checked");
    expect(request).toHaveBeenLastCalledWith("POST", "/staff/finance/account-codes/proposals/map-1/decision", { decision: "approved", reason: "Checked" });
  });

  it("turns the API's refusals into the screen's codes", () => {
    const invalid = toAccountCodesError(
      new ApiError("validation_failed", 422, [
        { field_path: ["accounts", "bank.trust"], code: "bad_sage_account" },
        { field_path: ["tax_rate"], code: "required" },
      ]),
    );
    expect(invalid.code).toBe("invalid");
    expect(invalid.problems).toEqual([
      { on: "bank.trust", code: "bad_sage_account" },
      { on: "tax_rate", code: "required" },
    ]);
    expect(toAccountCodesError(new ApiError("confirm_it_is_you", 403)).code).toBe("not_confirmed");
    expect(toAccountCodesError(new ApiError("conflict", 409)).code).toBe("conflict");
    expect(toAccountCodesError(new Error("network"))).toEqual(new AccountCodesError("unavailable"));
  });
});
