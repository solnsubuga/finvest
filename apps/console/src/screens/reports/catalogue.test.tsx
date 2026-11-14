import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Http } from "@/api/http.ts";
import { STUB_ROLE_PERMISSIONS } from "@/auth/stub.ts";
import { notConnectedAuditApi } from "./api.ts";
import { notConnectedReportsApi, periodProblem, reportsErrorMessage, toReportsError, type ReportFile, type ReportsApi } from "./catalogue.ts";
import { createHttpReportsApi } from "./catalogue-http.ts";
import { createStubReportsApi } from "./catalogue-stub.ts";
import type { ReportLink } from "./Catalogue.tsx";
import { readLink, ReportsScreen } from "./index.tsx";

// 09:00 in Kampala on Friday 13 November 2026.
const NOW = Date.parse("2026-11-13T06:00:00Z");
const ADMIN = { id: "stub-admin", name: "Solomon N.", permissions: STUB_ROLE_PERMISSIONS.admin };

function renderReports({ api = createStubReportsApi(ADMIN, () => NOW), me = ADMIN.id, link = null }: { api?: ReportsApi; me?: string; link?: ReportLink | null } = {}) {
  const save = vi.fn<(file: ReportFile) => void>();
  let key = 0;
  render(<ReportsScreen api={notConnectedAuditApi} canReadAudit={false} reports={{ api, me, link, save, newKey: () => `key-${++key}` }} />);
  return { save, user: userEvent.setup() };
}

const cards = () => screen.findByRole("list", { name: "Reports" });
const card = async (name: string) => within(await cards()).getByRole("listitem", { name });
const scheduleList = () => screen.findByRole("list", { name: "Schedules" });

describe("reports", () => {
  it("lists every report an admin may download, with how each is sent", async () => {
    renderReports();
    expect(within(await cards()).getAllByRole("listitem")).toHaveLength(7);
    const position = await card("Daily position");
    expect(await within(position).findByText("Emailed daily at 07:00 to Daniel Opio and Solomon N.")).toBeInTheDocument();
    expect(within(await card("Deposits and withdrawals")).getByText("1 waiting for approval")).toBeInTheDocument();
    expect(within(await card("Maturity ladder")).getByText("Not scheduled")).toBeInTheDocument();
  });

  it("offers a role only the reports its screens allow", async () => {
    const support = { id: "stub-support", name: "Tom Byaruhanga", permissions: [...STUB_ROLE_PERMISSIONS.support, "reports.view" as const] };
    renderReports({ api: createStubReportsApi(support, () => NOW), me: support.id });
    const names = within(await cards())
      .getAllByRole("heading")
      .map((heading) => heading.textContent);
    expect(names).toEqual(["Client statements", "Complaints for the regulator"]);
  });

  it("downloads a report for the period offered first", async () => {
    const { save, user } = renderReports();
    const position = await card("Daily position");
    await user.click(within(position).getByRole("button", { name: "Download" }));
    expect(within(position).getByLabelText("Day")).toHaveValue("2026-11-12");
    await user.selectOptions(within(position).getByLabelText("Currency"), "USD");
    await user.click(within(position).getByRole("button", { name: "Download CSV" }));
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0]?.[0].filename).toBe("finvest-daily-position-usd-2026-11-12.csv");
    expect(within(position).getByRole("status")).toHaveTextContent("on the audit trail");
  });

  it("refuses a period the report cannot be made for before asking", async () => {
    const { user } = renderReports();
    const deposits = await card("Deposits and withdrawals");
    await user.click(within(deposits).getByRole("button", { name: "Download" }));
    const from = within(deposits).getByLabelText("From");
    await user.clear(from);
    await user.type(from, "2026-09-01");
    expect(within(deposits).getByText("Choose up to 31 days.")).toBeInTheDocument();
    expect(within(deposits).getByRole("button", { name: "Download CSV" })).toBeDisabled();
  });

  it("picks whole months and quarters", async () => {
    const { save, user } = renderReports();
    const complaints = await card("Complaints for the regulator");
    await user.click(within(complaints).getByRole("button", { name: "Download" }));
    expect(within(complaints).getByLabelText("Quarter")).toHaveDisplayValue("Q3 2026, Jul to Sep");
    await user.selectOptions(within(complaints).getByLabelText("Quarter"), "2026-04-01");
    await user.click(within(complaints).getByRole("button", { name: "Download CSV" }));
    expect(save.mock.calls[0]?.[0].filename).toBe("finvest-complaints-quarterly-2026-04-01-to-2026-06-30.csv");

    const interest = await card("Interest and tax withheld");
    await user.click(within(interest).getByRole("button", { name: "Download" }));
    expect(within(interest).getByLabelText("Month")).toHaveValue("2026-10");
  });

  it("opens the report and period an emailed link names", async () => {
    renderReports({ link: { report: "deposits_and_withdrawals", from: "2026-11-02", to: "2026-11-08", currency: "USD" } });
    const deposits = await card("Deposits and withdrawals");
    expect(within(deposits).getByRole("button", { name: "Download" })).toHaveAttribute("aria-expanded", "true");
    expect(within(deposits).getByLabelText("From")).toHaveValue("2026-11-02");
    expect(within(deposits).getByLabelText("To")).toHaveValue("2026-11-08");
    expect(within(deposits).getByLabelText("Currency")).toHaveValue("USD");
  });

  it("says so when the link is for a report the role cannot download", async () => {
    const finance = { id: "stub-finance", name: "Daniel Opio", permissions: STUB_ROLE_PERMISSIONS.finance };
    renderReports({ api: createStubReportsApi(finance, () => NOW), me: finance.id, link: { report: "identity_and_risk", from: null, to: null, currency: null } });
    expect(await screen.findByText("The link you followed is for a report your role cannot download.")).toBeInTheDocument();
  });

  it("reads the link from the address", () => {
    expect(readLink("?report=maturity_ladder&from=2026-11-13&to=2026-11-19&currency=UGX")).toEqual({ report: "maturity_ladder", from: "2026-11-13", to: "2026-11-19", currency: "UGX" });
    expect(readLink("?sample=empty")).toBeNull();
  });

  it("proposes a schedule, which waits for a different person", async () => {
    const { user } = renderReports();
    const ladder = await card("Maturity ladder");
    await user.click(within(ladder).getByRole("button", { name: "Schedule" }));
    await user.selectOptions(within(ladder).getByLabelText("How often"), "weekly");
    // Support cannot read maturities, so is not offered.
    expect(within(ladder).queryByLabelText("Tom Byaruhanga")).not.toBeInTheDocument();
    await user.click(within(ladder).getByLabelText("Daniel Opio"));
    expect(within(ladder).getByRole("button", { name: "Propose schedule" })).toBeDisabled();
    await user.type(within(ladder).getByLabelText("Reason"), "Cash planning for the week");
    await user.click(within(ladder).getByRole("button", { name: "Propose schedule" }));
    expect(within(ladder).getByRole("status")).toHaveTextContent("Nothing is sent until a different member of staff approves it");
    expect(await within(ladder).findByText("1 waiting for approval")).toBeInTheDocument();
    const mine = within(await scheduleList()).getByRole("listitem", { name: /Maturity ladder, Waiting for approval/ });
    expect(within(mine).getByText("Emailed every Monday at 07:00 to Daniel Opio")).toBeInTheDocument();
    expect(within(mine).getByRole("button", { name: "Withdraw" })).toBeInTheDocument();
    expect(within(mine).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("approves a colleague's proposal with a reason", async () => {
    const { user } = renderReports();
    const waiting = within(await scheduleList()).getByRole("listitem", { name: /Deposits and withdrawals, Waiting for approval/ });
    await user.click(within(waiting).getByRole("button", { name: "Approve" }));
    expect(within(waiting).getAllByRole("button", { name: "Approve" }).at(-1)).toBeDisabled();
    await user.type(within(waiting).getByLabelText("Reason"), "Agreed");
    await user.click(within(waiting).getAllByRole("button", { name: "Approve" }).at(-1) as HTMLElement);
    expect(await screen.findByText("Deposits and withdrawals: Approved. The first email goes at 07:00 on the next day it is due.")).toBeInTheDocument();
    expect(within(await scheduleList()).getByRole("listitem", { name: /Deposits and withdrawals, Running/ })).toBeInTheDocument();
  });

  it("stops a running schedule", async () => {
    const { user } = renderReports();
    const running = within(await scheduleList()).getByRole("listitem", { name: /Identity and risk, Running/ });
    await user.click(within(running).getByRole("button", { name: "Stop sending" }));
    await user.type(within(running).getByLabelText("Reason"), "No longer needed");
    await user.click(within(running).getAllByRole("button", { name: "Stop sending" }).at(-1) as HTMLElement);
    expect(await within(await scheduleList()).findByRole("listitem", { name: /Identity and risk, Stopped/ })).toBeInTheDocument();
  });

  it("shows an empty list of schedules", async () => {
    renderReports({ api: createStubReportsApi(ADMIN, () => NOW, { empty: true }) });
    expect(await screen.findByText("No schedules yet")).toBeInTheDocument();
    expect(within(await card("Daily position")).getByText("Not scheduled")).toBeInTheDocument();
  });

  it("says when Finvest cannot be reached", async () => {
    renderReports({ api: notConnectedReportsApi });
    expect(await screen.findByText("Reports could not be loaded")).toBeInTheDocument();
    expect(await screen.findByText("Schedules could not be loaded")).toBeInTheDocument();
  });
});

describe("report rules", () => {
  it("checks periods the way the API does", () => {
    expect(periodProblem("days", "2026-11-01", "2026-11-13", "2026-11-13")).toBeNull();
    expect(periodProblem("days", "2026-11-14", "2026-11-14", "2026-11-13")).toBe("That period has not started yet.");
    expect(periodProblem("ahead", "2026-11-14", "2026-12-20", "2026-11-13")).toBeNull();
    expect(periodProblem("ahead", "2026-11-14", "2027-03-01", "2026-11-13")).toBe("Choose up to 92 days.");
    expect(periodProblem("now", "2026-11-12", "2026-11-12", "2026-11-13")).toMatch(/only be made for today/);
    expect(periodProblem("day", "2026-11-12", "2026-11-11", "2026-11-13")).toMatch(/on or after/);
  });

  it("words the API's refusals", () => {
    const refused = (code: string, status = 409) => new ApiError(status === 403 ? "forbidden" : "conflict", status, [{ field_path: ["body"], code }]);
    expect(reportsErrorMessage(refused("proposed_by_you", 403))).toMatch(/a different person must approve/);
    expect(reportsErrorMessage(new ApiError("validation_failed", 422, [{ field_path: ["date_to"], code: "too_many_rows" }]))).toMatch(/20,000 rows/);
    expect(toReportsError(new ApiError("validation_failed", 422, [{ field_path: ["date_from"], code: "whole_month_only" }])).code).toBe("period");
    expect(reportsErrorMessage(new ApiError("forbidden", 403))).toBe("Your role cannot do this.");
    expect(reportsErrorMessage(new ApiError("unavailable", 0))).toMatch(/could not be reached/);
  });
});

describe("reports over HTTP", () => {
  function fakeHttp() {
    const calls: { method: string; path: string; body: unknown; key: string | undefined }[] = [];
    const schedule = { id: "-Nschedule", report_key: "daily_position", report_name: "Daily position", frequency: "daily", currency: "UGX", recipients: ["a"], recipient_names: ["A"], reason: "r", status: "proposed", proposed_by: "a", proposed_by_name: "A", proposed_at: "2026-11-13T06:00:00Z", decided_by: null, decided_at: null, decision_reason: null, stopped_by: null, stopped_at: null, last_sent_at: null, yours: true };
    const http: Http = {
      async request<T>(method: string, path: string, body?: unknown, init?: { idempotencyKey?: string }) {
        calls.push({ method, path, body, key: init?.idempotencyKey });
        if (path === "/staff/reports") {
          return { today: "2026-11-13", reports: [{ key: "daily_position", name: "Daily position", about: "x", period: "day", by_currency: true, date_from: "2026-11-12", date_to: "2026-11-12" }, { key: "client_statements", name: "Client statements", about: "y", period: "month", by_currency: false, date_from: "2026-10-01", date_to: "2026-10-31" }] } as T;
        }
        return schedule as T;
      },
      async download(path: string) {
        calls.push({ method: "GET", path, body: undefined, key: undefined });
        return new Blob(["a,b\r\n"]);
      },
    };
    return { http, calls };
  }

  it("sends the routes the backend serves", async () => {
    const { http, calls } = fakeHttp();
    const api = createHttpReportsApi(http);
    const catalogue = await api.catalogue();
    expect(catalogue.reports[0]).toMatchObject({ key: "daily_position", byCurrency: true, from: "2026-11-12" });
    const position = await api.download("daily_position", { from: "2026-11-12", to: "2026-11-12", currency: "USD" });
    expect(position.filename).toBe("finvest-daily-position-usd-2026-11-12.csv");
    const statements = await api.download("client_statements", { from: "2026-10-01", to: "2026-10-31", currency: "UGX" });
    expect(statements.filename).toBe("finvest-client-statements-2026-10-01-to-2026-10-31.csv");
    expect(calls[1]?.path).toBe("/staff/reports/daily_position.csv?date_from=2026-11-12&date_to=2026-11-12&currency=USD");
    await api.propose({ reportKey: "daily_position", frequency: "daily", currency: "UGX", recipients: ["a"], reason: "r" }, "k1");
    expect(calls[3]).toMatchObject({ method: "POST", path: "/staff/report-schedules", key: "k1", body: { report_key: "daily_position", frequency: "daily", currency: "UGX", recipients: ["a"], reason: "r" } });
    await api.decide("-Nschedule", "approve", "ok", "k2");
    expect(calls[4]).toMatchObject({ path: "/staff/report-schedules/-Nschedule/approve", key: "k2", body: { reason: "ok" } });
    await api.decide("-Nschedule", "stop", "done", "k3");
    expect(calls[5]).toMatchObject({ path: "/staff/report-schedules/-Nschedule/stop", key: undefined });
  });
});
