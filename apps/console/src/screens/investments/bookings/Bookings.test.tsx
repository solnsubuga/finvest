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
