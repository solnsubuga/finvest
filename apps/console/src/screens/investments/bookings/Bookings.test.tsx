import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { createStubDirectory } from "@/directory/stub.ts";
import { notConnectedBookingsApi, type BookingsApi } from "./api.ts";
import { Bookings } from "./Bookings.tsx";
import { createStubBookingsApi } from "./stub.ts";

const ME = { id: "sample-me", name: "Amina Kintu" };

function renderBookings({ api = createStubBookingsApi(ME.id, "default", () => "2026-11-14T09:00:00Z"), canChange = true }: { api?: BookingsApi; canChange?: boolean } = {}) {
  let n = 0;
  render(<Bookings api={api} directory={createStubDirectory(ME)} me={ME.id} canChange={canChange} newKey={() => `key-${++n}`} />);
  return { user: userEvent.setup() };
}

const JOAN = /UGX\s?3,000,000 in the 182-day Treasury bill for Joan Namutebi/;

describe("investments: booking for a client", () => {
  it("lists bookings, open ones first, with the client and who booked it", async () => {
    renderBookings();
    const open = await screen.findByRole("article", { name: JOAN });
    expect(open).toHaveTextContent("Waiting for a second person");
    expect(open).toHaveTextContent("Pay the interest, keep the rest invested");
    await waitFor(() => expect(open).toHaveTextContent("Grace Auma"));
    expect(screen.getByRole("article", { name: /for Peter Okello/ })).toHaveTextContent("Waiting for the client to pay");
  });

  it("approves someone else's booking, which saves it for the client to pay", async () => {
    const { user } = renderBookings();
    const open = await screen.findByRole("article", { name: JOAN });
    await user.click(within(open).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getByRole("article", { name: JOAN })).toHaveTextContent("Saved and the client was told"));
  });

  it("sends a booking back only with a note", async () => {
    const { user } = renderBookings();
    const open = await screen.findByRole("article", { name: JOAN });
    await user.click(within(open).getByRole("button", { name: "Send back" }));
    const form = within(open).getByRole("form", { name: "Send the booking back" });
    await user.click(within(form).getByRole("button", { name: "Send back" }));
    expect(form).toHaveTextContent("Fill this in.");
    await user.type(within(form).getByLabelText(/Why send it back/), "She wants the 91-day bill");
    await user.click(within(form).getByRole("button", { name: "Send back" }));
    await waitFor(() => expect(screen.getByRole("article", { name: JOAN })).toHaveTextContent("Sent back by you"));
  });

  it("books for a client with a product, an amount and a reason", async () => {
    const { user } = renderBookings();
    await user.click(await screen.findByRole("button", { name: "Book an investment" }));
    const form = screen.getByRole("form", { name: "Book an investment for a client" });
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    expect(within(form).getAllByText("Fill this in.").length).toBeGreaterThanOrEqual(4);
    await user.type(within(form).getByLabelText(/Client/), "Sarah");
    await user.click(await within(form).findByRole("button", { name: /Sarah Achieng/ }));
    await user.selectOptions(within(form).getByLabelText(/Product/), "TREA-91");
    await user.type(within(form).getByLabelText(/Amount/), "50,000");
    expect(form).toHaveTextContent("This is below the product's minimum.");
    await user.clear(within(form).getByLabelText(/Amount/));
    await user.type(within(form).getByLabelText(/Amount/), "2,000,000");
    await user.selectOptions(within(form).getByLabelText(/When the term ends/), "pay_out");
    await user.type(within(form).getByLabelText(/How did the client ask/), "Called on Saturday morning.");
    await user.click(within(form).getByRole("button", { name: "Send for approval" }));
    const booked = await screen.findByRole("article", { name: /UGX\s?2,000,000 in the 91-day Treasury bill for Sarah Achieng/ });
    expect(booked).toHaveTextContent("Pay it out");
    expect(within(booked).getByRole("button", { name: "Withdraw it" })).toBeInTheDocument();
  });

  it("books every ready row of a spreadsheet, and says why the others cannot be", async () => {
    const { user } = renderBookings();
    await user.click(await screen.findByRole("button", { name: "Upload a spreadsheet" }));
    const form = screen.getByRole("form", { name: "Book investments from a spreadsheet" });
    const csv = [
      "account_number,product_code,amount,at_maturity",
      "1001006044,TREA-91,\"2,000,000\",pay_out",
      "1001003318,TREA-364,500000,",
      "1001002291,TREA-91,500000,",
      "1001004827,TREA-91,5000,",
    ].join("\n");
    await user.upload(within(form).getByLabelText("Spreadsheet"), new File([csv], "bookings.csv", { type: "text/csv" }));
    const table = await within(form).findByRole("table", { name: "Rows to book" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("Sarah Achieng");
    expect(rows[0]).toHaveTextContent("Ready");
    expect(rows[1]).toHaveTextContent("Joan Namutebi");
    expect(rows[2]).toHaveTextContent("No approved, active client has this account number.");
    expect(rows[3]).toHaveTextContent("Below the product's minimum.");
    await user.click(within(form).getByRole("button", { name: "Send 2 for approval" }));
    expect(form).toHaveTextContent("Fill this in.");
    await user.type(within(form).getByLabelText(/How did the clients ask/), "Asked at the Kira Road savings meeting on 13 November.");
    await user.click(within(form).getByRole("button", { name: "Send 2 for approval" }));
    await waitFor(() => expect(form).toHaveTextContent("2 sent for approval."));
    const booked = await screen.findByRole("article", { name: /UGX\s?2,000,000 in the 91-day Treasury bill for Sarah Achieng/ });
    expect(booked).toHaveTextContent("(row 2 of the uploaded sheet)");
    expect(screen.getByRole("article", { name: /in the 364-day Treasury bill for Joan Namutebi/ })).toHaveTextContent("Roll it over");
    expect(within(form).queryByRole("button", { name: /for approval/ })).toBeNull();
  });

  it("refuses a file that is not the template's shape", async () => {
    const { user } = renderBookings();
    await user.click(await screen.findByRole("button", { name: "Upload a spreadsheet" }));
    const form = screen.getByRole("form", { name: "Book investments from a spreadsheet" });
    await user.upload(within(form).getByLabelText("Spreadsheet"), new File(["name,amount\nJoan,5"], "x.csv", { type: "text/csv" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("Missing: account_number, product_code.");
  });

  it("shows nothing booked on a new system", async () => {
    renderBookings({ api: createStubBookingsApi(ME.id, "empty") });
    expect(await screen.findByText("Nothing has been booked for a client yet.")).toBeInTheDocument();
  });

  it("says so when Finvest cannot be reached, and offers no button", async () => {
    renderBookings({ api: notConnectedBookingsApi });
    expect(await screen.findByRole("alert")).toHaveTextContent("not connected");
    expect(screen.queryByRole("button", { name: "Book an investment" })).toBeNull();
  });

  it("lets someone without the permission see but not decide", async () => {
    renderBookings({ canChange: false });
    const open = await screen.findByRole("article", { name: JOAN });
    expect(within(open).queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Book an investment" })).toBeNull();
  });
});
