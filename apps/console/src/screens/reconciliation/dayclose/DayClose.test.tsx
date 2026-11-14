import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createHttp } from "@/api/http.ts";
import { DayCloseError, dayCloseErrorMessage, type DayCloseApi } from "./api.ts";
import { DayClose } from "./DayClose.tsx";
import { createHttpDayCloseApi, toDay } from "./http.ts";
import { createStubDayCloseApi, type DayCloseStubCase } from "./stub.ts";

const ME = "sample-me";
const NOW = new Date("2026-11-13T09:00:00Z");
const now = () => NOW;

function renderDay(which: DayCloseStubCase = "sample", api: DayCloseApi = createStubDayCloseApi(ME, 0, which, now)) {
  render(<DayClose api={api} canAct now={now} />);
  return { api, user: userEvent.setup() };
}

const status = () => screen.findByRole("region", { name: "Day close" });
const pot = (name: string) => screen.findByRole("article", { name });

function csv(text = "Date,Details,Reference,In,Out\n2026-11-12,TRANSFER,FT1,100000,\n") {
  return new File([text], "mtn.csv", { type: "text/csv" });
}

describe("closing the day", () => {
  it("opens yesterday and says why it cannot be prepared yet", async () => {
    renderDay();
    const close = await status();
    expect(within(close).getByRole("heading", { name: "Not ready to prepare" })).toBeInTheDocument();
    expect(within(close).getByText("A pot's statement for this day has not been imported.")).toBeInTheDocument();
    expect(within(close).queryByRole("button", { name: "Prepare the close" })).not.toBeInTheDocument();
    const mtn = await pot("ioTec wallet, MTN");
    expect(within(mtn).getByText("Statement missing")).toBeInTheDocument();
    const trust = await pot("Trust bank account, Stanbic trust");
    expect(within(trust).getAllByText("On the statement, not in the ledger")).toHaveLength(2);
    expect(within(trust).getByText("In the ledger, not on the statement")).toBeInTheDocument();
    expect(within(trust).getByText("MONTHLY SERVICE CHARGE", { exact: false })).toBeInTheDocument();
    expect(within(trust).getByText("Explained")).toBeInTheDocument();
  });

  it("imports a statement, pairs a line by hand and prepares the close", async () => {
    const { user } = renderDay();
    const mtn = await pot("ioTec wallet, MTN");
    await user.click(within(mtn).getByRole("button", { name: "Import the statement" }));
    await user.type(within(mtn).getByLabelText("Opening balance"), "12,400,000");
    await user.type(within(mtn).getByLabelText("Closing balance"), "15,150,000");
    await user.upload(within(mtn).getByLabelText("Statement file"), csv());
    await user.click(within(mtn).getByRole("button", { name: "Import" }));
    expect(await within(await pot("ioTec wallet, MTN")).findByText("Agrees")).toBeInTheDocument();

    const trust = await pot("Trust bank account, Stanbic trust");
    await user.click(within(trust).getByRole("button", { name: "Pair by hand" }));
    const form = within(trust).getByRole("form", { name: "Pair by hand" });
    await user.click(within(form).getByRole("button", { name: "Pair them" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("Say how you know they are the same money");
    await user.type(within(form).getByLabelText("How you know they are the same money"), "Same client, the bank cut the reference");
    await user.click(within(form).getByRole("button", { name: "Pair them" }));
    expect(await within(await status()).findByText("Paired. The difference is no longer open.")).toBeInTheDocument();
    expect(within(await pot("Trust bank account, Stanbic trust")).queryByText("TRANSFER FROM S NAMUKASA", { exact: false })).not.toBeInTheDocument();

    const ready = await status();
    expect(within(ready).getByRole("heading", { name: "Ready to prepare" })).toBeInTheDocument();
    expect(within(ready).getByText(/One open difference will be carried to the next day/)).toBeInTheDocument();
    await user.click(within(ready).getByRole("button", { name: "Prepare the close" }));
    const prepared = await screen.findByRole("heading", { name: "Prepared by you, waiting for a second person" });
    expect(prepared).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve and close the day" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Import the statement" })).not.toBeInTheDocument();
  });

  it("puts a refused balance next to its field", async () => {
    const { user } = renderDay();
    const mtn = await pot("ioTec wallet, MTN");
    await user.click(within(mtn).getByRole("button", { name: "Import the statement" }));
    await user.type(within(mtn).getByLabelText("Opening balance"), "12,000,000");
    await user.type(within(mtn).getByLabelText("Closing balance"), "15,150,000");
    await user.upload(within(mtn).getByLabelText("Statement file"), csv());
    await user.click(within(mtn).getByRole("button", { name: "Import" }));
    expect(await within(mtn).findByText("The opening balance is not the closing balance of this pot's last statement.")).toBeInTheDocument();
    expect(within(mtn).getByLabelText("Opening balance")).toHaveAttribute("aria-invalid", "true");
  });

  it("lets a second person approve, which closes the day", async () => {
    const { user } = renderDay("prepared");
    const close = await status();
    expect(within(close).getByRole("heading", { name: "Prepared, waiting for your approval" })).toBeInTheDocument();
    await user.click(within(close).getByRole("button", { name: "Approve and close the day" }));
    expect(await screen.findByRole("heading", { name: "Thu, 12 Nov 2026 is closed" })).toBeInTheDocument();
    expect(screen.getByText(/you approved it/)).toBeInTheDocument();
  });

  it("asks why before sending a close back", async () => {
    const { user } = renderDay("prepared");
    const close = await status();
    await user.click(within(close).getByRole("button", { name: "Send back" }));
    await user.click(within(close).getByRole("button", { name: "Send it back" }));
    expect(within(close).getByRole("alert")).toHaveTextContent("Say why");
    await user.type(within(close).getByLabelText("Why you are sending it back"), "The fee should be posted first");
    await user.click(within(close).getByRole("button", { name: "Send it back" }));
    expect(await screen.findByText("Sent back. The day can be prepared again.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ready to prepare" })).toBeInTheDocument();
  });

  it("moves to the first day not yet closed", async () => {
    const api = createStubDayCloseApi(ME, 0, "sample", now);
    const getDay = vi.spyOn(api, "getDay");
    render(<DayClose api={api} canAct now={() => new Date("2026-11-15T09:00:00Z")} />);
    await screen.findByRole("heading", { name: "Not ready to prepare" });
    expect(screen.getByLabelText("Business day")).toHaveValue("2026-11-12");
    expect(getDay.mock.calls.map((call) => call[0])).toEqual(["2026-11-14", "2026-11-12"]);
  });

  it("lets someone who can only see reconciliation read the day, and nothing more", async () => {
    render(<DayClose api={createStubDayCloseApi(ME, 0, "prepared", now)} canAct={false} now={now} />);
    const close = await status();
    expect(within(close).getByRole("heading", { name: "Prepared, waiting for a second person" })).toBeInTheDocument();
    expect(within(close).getByText(/You can see this day/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve and close the day" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send back" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Refresh the matches" })).not.toBeInTheDocument();
  });

  it("shows a failure with a way to try again", async () => {
    renderDay("error");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("We could not reach Finvest");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

function reply(status: number, body?: unknown) {
  return Promise.resolve(
    body === undefined ? new Response(null, { status }) : new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
}

/** The API's envelope: `conflict` or `validation_failed`, with the reason as the field problem's code. */
const refusal = (status: number, code: string, reason: string, path: (string | number)[]) =>
  reply(status, { request_id: "req", error: { code, message: code, fields: [{ field_path: path, code: reason }] } });

describe("closing the day over HTTP", () => {
  const http = (fetch: typeof globalThis.fetch) => createHttpDayCloseApi(createHttp("https://api.test", fetch, () => Promise.resolve(false)), ME);

  it("sends a statement as a multipart form, letting the browser set the boundary", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(201, { id: "s1", source: "iotec", account_reference: "mtn", business_date: "2026-11-12", opening: { minor: 1, currency: "UGX" }, closing: { minor: 1, currency: "UGX" }, line_count: 3 }));
    const made = await http(fetch).importStatement({
      source: "iotec",
      accountReference: "mtn",
      statementDate: "2026-11-12",
      openingBalance: "12,400,000",
      closingBalance: "15,150,000",
      file: csv(),
    });
    expect(made).toEqual({ id: "s1", businessDate: "2026-11-12", lineCount: 3 });
    const [url, init] = fetch.mock.calls[0] ?? [];
    expect(url).toBe("https://api.test/staff/reconciliation/statements");
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
    const form = init?.body as FormData;
    expect(form).toBeInstanceOf(FormData);
    expect(Object.fromEntries([...form.entries()].filter(([name]) => name !== "file"))).toEqual({
      source: "iotec",
      account_reference: "mtn",
      statement_date: "2026-11-12",
      opening_balance: "12,400,000",
      closing_balance: "15,150,000",
      format: "standard",
    });
    expect((form.get("file") as File).name).toBe("mtn.csv");
  });

  it("pairs by the movement's entry and line, and signs the day", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(() => reply(204));
    const days = http(fetch);
    await days.match({
      statementLineId: "-Nline",
      movement: { id: "-Nentry:2", entryId: "-Nentry", lineNo: 2, effectiveDate: "2026-11-12", direction: "in", amount: { minor: 5, currency: "UGX" }, reference: "r", entryType: "payment.deposit_confirmed" },
      reason: "Same money",
    });
    await days.refresh("2026-11-12");
    await days.sendBack("2026-11-12", "Post the fee first");
    expect(fetch.mock.calls.map((call) => [call[0], call[1]?.method, call[1]?.body])).toEqual([
      ["https://api.test/staff/reconciliation/matches", "POST", JSON.stringify({ statement_line_id: "-Nline", entry_id: "-Nentry", line_no: 2, reason: "Same money" })],
      ["https://api.test/staff/reconciliation/days/2026-11-12/refresh", "POST", null],
      ["https://api.test/staff/reconciliation/days/2026-11-12/send-back", "POST", JSON.stringify({ reason: "Post the fee first" })],
    ]);
  });

  it("words the backend's refusals by the code in the field problem, with the file's row", async () => {
    const cases: [() => Promise<Response>, string][] = [
      [() => refusal(409, "conflict", "close_needs_someone_who_did_not_import", ["body"]), "someone who imported none of them"],
      [() => refusal(422, "validation_failed", "statement_date_invalid", ["body", "file", 4]), "Row 4 has a date that is not written as YYYY-MM-DD."],
      [() => refusal(409, "conflict", "a_code_from_later", ["body"]), "Someone else has acted on this day."],
      [() => Promise.reject(new TypeError("offline")), "could not reach Finvest"],
    ];
    for (const [response, words] of cases) {
      const error = await http(vi.fn<typeof globalThis.fetch>(response)).approve("2026-11-12").catch((caught: unknown) => caught);
      expect(error).toBeInstanceOf(DayCloseError);
      expect(dayCloseErrorMessage(error)).toContain(words);
    }
  });

  it("reads who signed, and keeps a blocker it does not know", () => {
    const day = toDay(
      {
        business_date: "2026-11-12",
        last_closed: "2026-11-11",
        close: { id: "c1", status: "prepared", prepared_by: ME, decided_by: null },
        blockers: ["statement_missing", "something_new"],
        positions: [],
      },
      ME,
    );
    expect(day.close).toEqual({ id: "c1", status: "prepared", preparedByYou: true, decidedByYou: null });
    expect(day.blockers).toEqual(["statement_missing", "other"]);
  });
});
