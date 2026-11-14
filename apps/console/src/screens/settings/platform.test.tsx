import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { inNetwork, remindersText, SettingsError, shillings } from "./platform.ts";
import { createHttpLimitsApi, createHttpPlatformSettingsApi, toSettingsError } from "./platformHttp.ts";
import { PlatformSections } from "./PlatformSections.tsx";
import { createStubLimitsApi, createStubPlatformSettingsApi, type PlatformSample } from "./platformStub.ts";

const ME = { id: "sample-me", name: "Amina Kintu" };
const NOW = Date.parse("2026-11-14T09:00:00Z");
const EVERYTHING = ["payment_channels.change", "calendar.change", "staff_security.change", "statement_layouts.change", "withdrawal_limits.change"];

function renderSections(seed: PlatformSample = "sample", permissions: readonly string[] = EVERYTHING) {
  const reader = { id: ME.id, permissions };
  const settings = createStubPlatformSettingsApi(reader, () => NOW, seed);
  const limits = createStubLimitsApi(reader, () => NOW, seed);
  render(<PlatformSections settings={settings} limits={limits} directory={createStubDirectory(ME)} />);
  return { settings, limits, user: userEvent.setup() };
}

const card = (name: string) => screen.findByRole("article", { name });

describe("settings: platform rules", () => {
  it("shows every section with the values in use and keeps identity approval human", async () => {
    renderSections("empty");
    const limits = await card("Limits and automatic approval");
    expect(await within(limits).findByText("UGX 5,000,000")).toBeInTheDocument();
    expect(within(limits).getByText("Off. A person reviews every client")).toBeInTheDocument();
    const channels = await card("Payment channels");
    expect(await within(channels).findByText("All open")).toBeInTheDocument();
    const calendar = await card("Calendar and timing");
    expect(within(calendar).getByText("14:00")).toBeInTheDocument();
    expect(within(calendar).getByText("7 days and 1 day before")).toBeInTheDocument();
    const security = await card("Staff security");
    expect(within(security).getByText("30 minutes")).toBeInTheDocument();
    expect(within(security).getByText("Any network")).toBeInTheDocument();
    expect(within(await card("Statement import")).getByText("Built-in layout only")).toBeInTheDocument();
  });

  it("approves someone else's holiday change with a reason", async () => {
    const { user } = renderSections("sample");
    const calendar = await card("Calendar and timing");
    const waiting = await within(calendar).findByRole("region", { name: /Public holidays: change waiting/ });
    expect(within(waiting).getByText("Eid al-Fitr")).toBeInTheDocument();
    await user.click(within(waiting).getByRole("button", { name: "Approve" }));
    await user.click(within(waiting).getByRole("button", { name: "Approve the change" }));
    expect(within(waiting).getByText("Write a short reason for the audit trail.")).toBeInTheDocument();
    await user.type(within(waiting).getByRole("textbox"), "Checked against the gazette");
    await user.click(within(waiting).getByRole("button", { name: "Approve the change" }));
    expect(await within(calendar).findByText("9 days, 2026 and 2027")).toBeInTheDocument();
    expect(within(calendar).queryByRole("region", { name: /Public holidays: change waiting/ })).not.toBeInTheDocument();
  });

  it("lets the proposer only withdraw their own change", async () => {
    renderSections("mine");
    const limits = await card("Limits and automatic approval");
    const waiting = await within(limits).findByRole("region", { name: /change waiting/ });
    expect(within(waiting).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(within(waiting).getByRole("button", { name: "Withdraw it" })).toBeInTheDocument();
  });

  it("proposes new limits from a later day and refuses a daily limit below one payout", async () => {
    const { user } = renderSections("empty");
    const limits = await card("Limits and automatic approval");
    await user.click(await within(limits).findByRole("button", { name: "Change limits in force" }));
    const form = within(limits).getByRole("form", { name: "New limits in force" });
    const perPayout = within(form).getByRole("textbox", { name: /Mobile money, one payout/ });
    await user.clear(perPayout);
    await user.type(perPayout, "3000000");
    await user.type(within(form).getByRole("textbox", { name: /Mobile money, per day/ }), "1000000");
    await user.type(within(form).getByRole("textbox", { name: /Why is it changing/ }), "Fraud review");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(within(form).getByText("The daily mobile money limit cannot be below the limit for one payout.")).toBeInTheDocument();
    const perDay = within(form).getByRole("textbox", { name: /Mobile money, per day/ });
    await user.clear(perDay);
    await user.type(perDay, "6000000");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    const waiting = await within(limits).findByRole("region", { name: /change waiting/ });
    expect(within(waiting).getByText("UGX 3,000,000")).toBeInTheDocument();
  });

  it("warns before a list of office networks leaves you out", async () => {
    const { user } = renderSections("empty");
    const security = await card("Staff security");
    await user.click(await within(security).findByRole("button", { name: "Change allowed office networks" }));
    const form = within(security).getByRole("form", { name: "New allowed office networks" });
    await user.type(within(form).getByRole("textbox", { name: /Address or range/ }), "10.0.0.0/24");
    await user.type(within(form).getByRole("textbox", { name: /^Name/ }), "Elsewhere");
    await user.click(within(form).getByRole("button", { name: "Add" }));
    await user.type(within(form).getByRole("textbox", { name: /Why is it changing/ }), "Office only");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(within(form).getByText(/leaves out the network you are on \(196\.43\.130\.27\)/)).toBeInTheDocument();
    await user.click(within(form).getByRole("button", { name: /Add the network you are on/ }));
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(await within(security).findByRole("region", { name: /Allowed office networks: change waiting/ })).toBeInTheDocument();
  });

  it("shows a section read only without its permission", async () => {
    renderSections("empty", ["payment_channels.change"]);
    const security = await card("Staff security");
    await within(security).findByText("30 minutes");
    expect(within(security).queryByRole("button", { name: /^Change/ })).not.toBeInTheDocument();
    expect(within(await card("Payment channels")).getByRole("button", { name: "Change channels open" })).toBeInTheDocument();
  });
});

describe("settings: platform helpers", () => {
  it("formats values the way staff read them", () => {
    expect(shillings(5_000_000)).toBe("UGX 5,000,000");
    expect(shillings(null)).toBe("Not set");
    expect(remindersText([1, 7])).toBe("7 days and 1 day before");
    expect(remindersText([14, 7, 1])).toBe("14 days, 7 days and 1 day before");
    expect(remindersText([])).toBe("None");
  });

  it("matches an address against a network", () => {
    expect(inNetwork("196.43.130.27", "196.43.130.0/24")).toBe(true);
    expect(inNetwork("196.43.131.27", "196.43.130.0/24")).toBe(false);
    expect(inNetwork("102.134.149.12", "102.134.149.12")).toBe(true);
    expect(inNetwork("2001:db8::1", "2001:db8::1")).toBe(true);
  });

  it("names the refusals the backend sends", () => {
    const conflict = (code: string) => new ApiError("conflict", 409, [{ field_path: ["value"], code }]);
    expect(toSettingsError(conflict("leaves_you_out")).code).toBe("leaves_you_out");
    expect(toSettingsError(conflict("day_taken")).code).toBe("day_taken");
    expect(toSettingsError(new ApiError("conflict", 409)).code).toBe("conflict");
    expect(toSettingsError(new ApiError("forbidden", 403), true).code).toBe("same_person");
    expect(toSettingsError(new ApiError("confirm_it_is_you", 403)).code).toBe("not_confirmed");
    const invalid = toSettingsError(new ApiError("validation_failed", 422, [{ field_path: ["body", "bank_payout_cutoff", "value"], code: "outside_banking_hours" }]));
    expect(invalid).toEqual(new SettingsError("invalid", [{ field: "value", code: "outside_banking_hours" }]));
  });
});

describe("settings: platform over the API", () => {
  function fakeHttp(responses: Record<string, unknown>) {
    const sent: { method: string; path: string; body: unknown }[] = [];
    const http: Http = {
      request: <T,>(method: string, path: string, body?: unknown) => {
        sent.push({ method, path, body });
        return Promise.resolve(responses[`${method} ${path}`] as T);
      },
      download: () => Promise.reject(new Error("unused")),
    } as Http;
    return { http, sent };
  }

  it("reads and sends channels and layouts in the backend's shape", async () => {
    const channels = { mtn_pay_in: true, airtel_pay_in: false, bank_pay_in: true, mtn_payout: true, airtel_payout: true, bank_payout: false };
    const { http, sent } = fakeHttp({
      "GET /staff/settings/platform": {
        settings: [
          { key: "payment_channels", section: "channels", value: channels, changed_at: null, changed_by: null, can_change: true, proposal: null },
          {
            key: "statement_layouts",
            section: "statement_import",
            value: [{ name: "mtn_csv", label: "MTN", date_column: "Date", date_format: "DD/MM/YYYY", narrative: "Details", amount: "Amount", direction: "Type", in_word: "CR", out_word: "DR" }],
            changed_at: "2026-11-02T08:15:00Z",
            changed_by: "staff-1",
            can_change: false,
            proposal: null,
          },
        ],
        your_address: "196.43.130.27",
        date_formats: ["DD/MM/YYYY"],
      },
    });
    const api = createHttpPlatformSettingsApi(http);
    const listed = await api.list();
    expect(listed.settings.payment_channels.value.airtelPayIn).toBe(false);
    expect(listed.settings.statement_layouts.value[0]).toMatchObject({ dateColumn: "Date", inWord: "CR", moneyIn: null, reference: null });
    await api.propose("payment_channels", { ...listed.settings.payment_channels.value, bankPayout: true }, " Bank is back ");
    expect(sent[1]).toEqual({
      method: "POST",
      path: "/staff/settings/platform/proposals",
      body: { key: "payment_channels", value: { ...channels, bank_payout: true }, reason: "Bank is back" },
    });
  });

  it("sends new limits in shillings with the start day", async () => {
    const { http, sent } = fakeHttp({});
    await createHttpLimitsApi(http).propose(
      { effectiveFrom: "2026-12-01", mobileMoneyPerPayout: 3_000_000, mobileMoneyPerDay: null, bankPerPayout: null, bankPerDay: null, reviewAbove: 8_000_000 },
      "Fraud review",
    );
    expect(sent[0]?.body).toEqual({
      effective_from: "2026-12-01",
      mobile_money_per_payout_minor: 3_000_000,
      mobile_money_per_day_minor: null,
      bank_per_payout_minor: null,
      bank_per_day_minor: null,
      review_above_minor: 8_000_000,
      reason: "Fraud review",
    });
  });
});
