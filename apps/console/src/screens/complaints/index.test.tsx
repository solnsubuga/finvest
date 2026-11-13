import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { createStubDirectory } from "@/directory/stub.ts";
import { ComplaintsError, notConnectedComplaintsApi, type ComplaintsApi } from "./api.ts";
import { ComplaintsScreen } from "./index.tsx";
import { dueLabel, received, tenthsOfDays } from "./labels.ts";
import { addWorkingDays, createStubComplaintsApi, workingDaysBetween } from "./stub.ts";

const ME = { id: "sample-me", name: "Amina Kintu" };
const TODAY = "2026-11-13";
const NOW = Date.parse("2026-11-13T09:00:00Z");

function renderComplaints({
  me = ME,
  api = createStubComplaintsApi(me, () => TODAY, () => NOW),
  canManage = true,
  canClose = true,
}: { me?: { id: string; name: string }; api?: ComplaintsApi; canManage?: boolean; canClose?: boolean } = {}) {
  let keys = 0;
  render(
    <ComplaintsScreen
      api={api}
      directory={createStubDirectory(ME)}
      me={me.id}
      canManage={canManage}
      canClose={canClose}
      newKey={() => `test-key-${++keys}`}
      today={() => TODAY}
    />,
  );
  return { user: userEvent.setup() };
}

const GRACE = { id: "sample-staff-grace", name: "Grace Auma" };

const figure = (name: string) => screen.getByText(name, { selector: "dt" }).parentElement as HTMLElement;
const panel = (name: string) => screen.findByRole("region", { name });

async function rows(): Promise<string[][]> {
  const table = await screen.findByRole("table", { name: "Complaints register" });
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) => [...row.querySelectorAll("th, td")].map((cell) => cell.textContent ?? ""));
}

describe("complaints screen", () => {
  it("shows the figures and the open register, earliest deadline first", async () => {
    renderComplaints();
    await panel("C-2611, Joan Namutebi");
    expect(figure("Open")).toHaveTextContent("5");
    expect(figure("Open")).toHaveTextContent("1 past its deadline");
    expect(figure("Average time to resolve")).toHaveTextContent("3.0 days");
    expect(figure("Average time to resolve")).toHaveTextContent("Target 5 working days");
    expect(figure("This quarter")).toHaveTextContent("6 complaints");
    expect(figure("This quarter")).toHaveTextContent("1 resolved");
    expect(figure("Upheld")).toHaveTextContent("1");
    await waitFor(async () =>
      expect(await rows()).toEqual([
        ["C-2611", "Joan Namutebi", "Payout did not arrive", "Grace", "12 Nov", "Past deadline"],
        ["C-2614", "Peter Okello", "Bank deposit not matched", "Grace", "16 Nov", "Investigating"],
        ["C-2615", "Sarah Achieng", "Rate was lower than expected", "Daniel", "17 Nov", "Investigating"],
        ["C-2616", "Ivan Ssemanda", "Could not sign in on a new phone", "No one yet", "18 Nov", "Waiting for client"],
        ["C-2617", "Kira Road Savings Group", "Signatory change taking too long", "Grace", "18 Nov", "New"],
      ]),
    );
  });

  it("opens a complaint with what the client said and its history", async () => {
    renderComplaints();
    const detail = await panel("C-2611, Joan Namutebi");
    expect(within(detail).getByText("5 Nov, by phone")).toBeInTheDocument();
    expect(within(detail).getByText("UGX 1,026,490")).toBeInTheDocument();
    expect(within(detail).getByText("12 Nov, past")).toBeInTheDocument();
    const history = within(detail).getByRole("list", { name: "History" });
    expect(within(history).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Logged, Grace Auma, 5 Nov, 10:20",
      "Assigned, Grace Auma, 5 Nov, 11:00",
      "Moved to investigating, Grace Auma, 5 Nov, 12:10MTN returned an error on the first try. Asked them why.",
    ]);
  });

  it("needs a note to escalate, then shows it in the history", async () => {
    const { user } = renderComplaints();
    const detail = await panel("C-2611, Joan Namutebi");
    await user.click(within(detail).getByRole("button", { name: "Escalate to a manager" }));
    const form = within(detail).getByRole("form", { name: "Change status" });
    expect(within(form).getByLabelText("Move it to")).toHaveValue("escalated");
    await user.click(within(form).getByRole("button", { name: "Escalate" }));
    expect(within(form).getByText("Write a short note for the record.")).toBeInTheDocument();
    await user.type(within(form).getByLabelText(/Why\?/), "Over a million and past the deadline.");
    await user.click(within(form).getByRole("button", { name: "Escalate" }));
    const after = await panel("C-2611, Joan Namutebi");
    const history = within(after).getByRole("list", { name: "History" });
    await waitFor(() =>
      expect(within(history).getAllByRole("listitem").at(-1)).toHaveTextContent(
        "Escalated, Amina Kintu, 13 Nov, 12:00Over a million and past the deadline.",
      ),
    );
    expect(within(after).queryByRole("button", { name: "Escalate to a manager" })).not.toBeInTheDocument();
  });

  it("gives a complaint to someone", async () => {
    const { user } = renderComplaints();
    await user.click(await screen.findByRole("button", { name: "C-2616, Could not sign in on a new phone" }));
    const detail = await panel("C-2616, Ivan Ssemanda");
    await user.selectOptions(within(detail).getByRole("combobox", { name: "Owner" }), "sample-staff-daniel");
    await waitFor(async () => expect((await rows())[3]?.[3]).toBe("Daniel"));
  });

  it("proposes an outcome only with what was done and the client's answer, and does not close it", async () => {
    const { user } = renderComplaints();
    const detail = await panel("C-2611, Joan Namutebi");
    await user.click(within(detail).getByRole("button", { name: "Propose the outcome" }));
    const form = within(detail).getByRole("form", { name: "Propose the outcome" });
    await user.click(within(form).getByRole("button", { name: "Send for confirmation" }));
    expect(within(form).getByText(/Choose an outcome, say what was done/)).toBeInTheDocument();
    await user.click(within(form).getByLabelText(/^Upheld/));
    await user.type(within(form).getByLabelText(/What was done about it/), "We sent the payout again and it arrived.");
    await user.click(within(form).getByLabelText("Yes"));
    await user.click(within(form).getByRole("button", { name: "Send for confirmation" }));
    const after = await panel("C-2611, Joan Namutebi");
    const waiting = await within(after).findByRole("group", { name: "Outcome waiting" });
    expect(waiting).toHaveTextContent("Proposed by you, 13 Nov, 12:00");
    expect(waiting).toHaveTextContent("Someone else confirms it.");
    // The proposer never confirms their own outcome, and only one waits at a time.
    expect(within(waiting).queryByRole("button", { name: "Confirm and close" })).not.toBeInTheDocument();
    expect(within(after).queryByRole("button", { name: "Propose the outcome" })).not.toBeInTheDocument();
    expect((await rows()).map((row) => row[0])).toContain("C-2611");
    // Withdrawing it lets them propose again.
    await user.click(within(waiting).getByRole("button", { name: "Withdraw it" }));
    const again = await panel("C-2611, Joan Namutebi");
    await waitFor(() => expect(within(again).queryByRole("group", { name: "Outcome waiting" })).not.toBeInTheDocument());
    expect(within(again).getByRole("button", { name: "Propose the outcome" })).toBeInTheDocument();
    const history = within(again).getByRole("list", { name: "History" });
    expect(within(history).getAllByRole("listitem").slice(-2).map((item) => item.textContent)).toEqual([
      "Outcome proposed, Amina Kintu, 13 Nov, 12:00",
      "Outcome withdrawn, Amina Kintu, 13 Nov, 12:00",
    ]);
  });

  it("closes a complaint when someone else confirms the outcome", async () => {
    const { user } = renderComplaints();
    await user.click(await screen.findByRole("button", { name: "C-2615, Rate was lower than expected" }));
    const detail = await panel("C-2615, Sarah Achieng");
    const waiting = within(detail).getByRole("group", { name: "Outcome waiting" });
    expect(waiting).toHaveTextContent("Not upheld. The client accepted this.");
    expect(waiting).toHaveTextContent("Proposed by Daniel Kato, 12 Nov, 12:40");
    await user.click(within(waiting).getByRole("button", { name: "Confirm and close" }));
    await waitFor(async () => expect((await rows()).map((row) => row[0])).not.toContain("C-2615"));
    expect(figure("This quarter")).toHaveTextContent("2 resolved");
    await user.click(screen.getByRole("tab", { name: "Closed" }));
    await user.click(await screen.findByRole("button", { name: "C-2615, Rate was lower than expected" }));
    const closed = await panel("C-2615, Sarah Achieng");
    expect(within(closed).getByText("Not upheld, closed 13 Nov")).toBeInTheDocument();
    expect(within(closed).getByText(/The rate you saw was right/)).toBeInTheDocument();
    expect(within(closed).queryByRole("button", { name: "Add a note" })).not.toBeInTheDocument();
  });

  it("needs a reason to reject an outcome, and then it can be proposed again", async () => {
    const { user } = renderComplaints();
    await user.click(await screen.findByRole("button", { name: "C-2615, Rate was lower than expected" }));
    const detail = await panel("C-2615, Sarah Achieng");
    await user.click(within(detail).getByRole("button", { name: "Reject" }));
    const form = within(detail).getByRole("form", { name: "Reject the outcome" });
    await user.click(within(form).getByRole("button", { name: "Reject it" }));
    expect(within(form).getByText("Write a short note for the record.")).toBeInTheDocument();
    await user.type(within(form).getByLabelText(/What needs to change/), "Say what the rate was on the day she invested.");
    await user.click(within(form).getByRole("button", { name: "Reject it" }));
    const after = await panel("C-2615, Sarah Achieng");
    await waitFor(() => expect(within(after).queryByRole("group", { name: "Outcome waiting" })).not.toBeInTheDocument());
    expect(within(after).getByRole("button", { name: "Propose the outcome" })).toBeInTheDocument();
    const history = within(after).getByRole("list", { name: "History" });
    expect(within(history).getAllByRole("listitem").at(-1)).toHaveTextContent(
      "Outcome rejected, Amina Kintu, 13 Nov, 12:00Say what the rate was on the day she invested.",
    );
  });

  it("never lets whoever logged a complaint confirm its outcome", async () => {
    const api = createStubComplaintsApi(GRACE, () => TODAY, () => NOW);
    const { user } = renderComplaints({ me: GRACE, api });
    await user.click(await screen.findByRole("button", { name: "C-2615, Rate was lower than expected" }));
    const waiting = within(await panel("C-2615, Sarah Achieng")).getByRole("group", { name: "Outcome waiting" });
    expect(waiting).toHaveTextContent("You logged this complaint, so someone else confirms its outcome.");
    expect(within(waiting).queryByRole("button")).not.toBeInTheDocument();
    // The stub refuses it too, as the API does.
    const { complaints } = await api.register("open");
    const id = complaints.find((complaint) => complaint.reference === "C-2615")?.id as string;
    const pending = (await api.complaint(id)).pendingOutcome;
    await expect(api.decideOutcome(id, pending?.id as string, "confirmed", null)).rejects.toMatchObject({ code: "forbidden" });
  });

  it("lets support work a complaint and propose its outcome, but not confirm one", async () => {
    const { user } = renderComplaints({ canClose: false });
    const detail = await panel("C-2611, Joan Namutebi");
    expect(within(detail).getByRole("button", { name: "Propose the outcome" })).toBeInTheDocument();
    expect(within(detail).getByRole("button", { name: "Add a note" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "C-2615, Rate was lower than expected" }));
    const waiting = within(await panel("C-2615, Sarah Achieng")).getByRole("group", { name: "Outcome waiting" });
    expect(waiting).toHaveTextContent("Someone who can close complaints confirms it.");
    expect(within(waiting).queryByRole("button")).not.toBeInTheDocument();
  });

  it("only shows the register to a role that can look but not act", async () => {
    renderComplaints({ canManage: false, canClose: false });
    const detail = await panel("C-2611, Joan Namutebi");
    expect(within(detail).queryByRole("button")).not.toBeInTheDocument();
    expect(within(detail).queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Log a complaint" })).not.toBeInTheDocument();
  });

  it("logs a complaint for a client found by name, due in 5 working days", async () => {
    const { user } = renderComplaints();
    await panel("C-2611, Joan Namutebi");
    await user.click(screen.getByRole("button", { name: "Log a complaint" }));
    const form = screen.getByRole("form", { name: "Log a complaint" });
    await user.click(within(form).getByRole("button", { name: "Log complaint" }));
    expect(within(form).getByText("Choose the client")).toBeInTheDocument();
    await user.type(within(form).getByLabelText("Client"), "dav");
    await user.click(await within(form).findByRole("button", { name: /David Mugisha/ }));
    await user.selectOptions(within(form).getByLabelText("About"), "identity");
    await user.selectOptions(within(form).getByLabelText("How it came in"), "phone");
    await user.type(within(form).getByLabelText("Summary"), "Selfie refused three times");
    await user.type(within(form).getByLabelText(/What the client said/), "The app keeps refusing my selfie.");
    await user.type(within(form).getByLabelText(/Amount in question/), "12.5");
    await user.click(within(form).getByRole("button", { name: "Log complaint" }));
    expect(within(form).getByText("Write the amount in whole units, like 150,000")).toBeInTheDocument();
    await user.clear(within(form).getByLabelText(/Amount in question/));
    await user.click(within(form).getByRole("button", { name: "Log complaint" }));
    const detail = await panel("C-2618, David Mugisha");
    expect(within(detail).getByText("13 Nov, by phone")).toBeInTheDocument();
    expect(within(detail).getByText("20 Nov")).toBeInTheDocument();
    expect(figure("Open")).toHaveTextContent("6");
  });

  it("logs a complaint once when the answer is lost and staff try again", async () => {
    const stub = createStubComplaintsApi(ME, () => TODAY, () => NOW);
    const keys: string[] = [];
    let lose = true;
    const api: ComplaintsApi = {
      ...stub,
      log: async (complaint) => {
        keys.push(complaint.clientKey);
        const value = await stub.log(complaint);
        if (lose) {
          lose = false;
          throw new ComplaintsError("unavailable");
        }
        return value;
      },
    };
    const { user } = renderComplaints({ api });
    await panel("C-2611, Joan Namutebi");
    await user.click(screen.getByRole("button", { name: "Log a complaint" }));
    const form = screen.getByRole("form", { name: "Log a complaint" });
    await user.type(within(form).getByLabelText("Client"), "dav");
    await user.click(await within(form).findByRole("button", { name: /David Mugisha/ }));
    await user.selectOptions(within(form).getByLabelText("About"), "identity");
    await user.selectOptions(within(form).getByLabelText("How it came in"), "phone");
    await user.type(within(form).getByLabelText("Summary"), "Selfie refused three times");
    await user.type(within(form).getByLabelText(/What the client said/), "The app keeps refusing my selfie.");
    await user.click(within(form).getByRole("button", { name: "Log complaint" }));
    expect(await within(form).findByText(/We could not reach Finvest/)).toBeInTheDocument();
    await user.click(within(form).getByRole("button", { name: "Log complaint" }));
    expect(await panel("C-2618, David Mugisha")).toBeInTheDocument();
    expect(keys).toEqual(["test-key-1", "test-key-1"]);
    expect(figure("Open")).toHaveTextContent("6");
  });

  it("opens a complaint logged from the closed list in the open list", async () => {
    const { user } = renderComplaints();
    await panel("C-2611, Joan Namutebi");
    await user.click(screen.getByRole("tab", { name: "Closed" }));
    await panel("C-2609, Amara Nakato");
    await user.click(screen.getByRole("button", { name: "Log a complaint" }));
    const form = screen.getByRole("form", { name: "Log a complaint" });
    await user.type(within(form).getByLabelText("Client"), "joan");
    await user.click(await within(form).findByRole("button", { name: /Joan Namutebi/ }));
    await user.selectOptions(within(form).getByLabelText("About"), "fees");
    await user.selectOptions(within(form).getByLabelText("How it came in"), "email");
    await user.type(within(form).getByLabelText("Summary"), "Charged twice");
    await user.type(within(form).getByLabelText(/What the client said/), "I was charged twice.");
    await user.click(within(form).getByRole("button", { name: "Log complaint" }));
    expect(await panel("C-2618, Joan Namutebi")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Open" })).toHaveAttribute("aria-selected", "true");
  });

  it("says when complaints are not connected", async () => {
    renderComplaints({ api: notConnectedComplaintsApi });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The register could not be loaded");
    expect(alert).toHaveTextContent("Complaints are not connected to the Finvest service yet.");
  });

  it("shows an empty register", async () => {
    const stub = createStubComplaintsApi(ME, () => TODAY, () => NOW);
    const api: ComplaintsApi = {
      ...stub,
      register: (show) => stub.register(show).then((register) => ({ ...register, complaints: [] })),
    };
    renderComplaints({ api });
    expect(await screen.findByRole("heading", { name: "No open complaints" })).toBeInTheDocument();
  });
});

describe("working days", () => {
  it.each([
    ["2026-11-05", 5, "2026-11-12"],
    ["2026-11-13", 5, "2026-11-20"],
    ["2026-11-14", 1, "2026-11-16"],
    ["2026-11-15", 5, "2026-11-20"],
    ["2026-12-31", 1, "2027-01-01"],
  ])("%s plus %i is %s", (start, days, due) => {
    expect(addWorkingDays(start, days)).toBe(due);
    expect(workingDaysBetween(start, due)).toBe(days);
  });
});

describe("labels", () => {
  it("reads how a complaint came in", () => {
    expect(received("phone")).toBe("by phone");
    expect(received("letter")).toBe("by letter");
    expect(received("in_person")).toBe("in person");
    expect(received("app")).toBe("in the app");
  });
  it("says today for a deadline today", () => {
    expect(dueLabel(TODAY, TODAY)).toBe("Today");
    expect(dueLabel("2026-11-16", TODAY)).toBe("16 Nov");
  });
  it("reads tenths of a day", () => {
    expect(tenthsOfDays(24)).toBe("2.4 days");
    expect(tenthsOfDays(10)).toBe("1.0 day");
    expect(tenthsOfDays(0)).toBe("0.0 days");
  });
});
