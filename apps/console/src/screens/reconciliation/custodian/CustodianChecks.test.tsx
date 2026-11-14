import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CustodianChecks } from "./CustodianChecks.tsx";
import { createStubCustodianChecksApi, type CustodianStubCase } from "./stub.ts";
import type { CustodianChecksApi } from "./api.ts";

const ME = "-Nstaff000000000000A";
const NOW = new Date("2026-11-14T09:00:00Z");
const REVIEW = "-OdCustodianCheck0003";
const WAITING = "-OdCustodianCheck0002";
const UNREADABLE = "-OdCustodianCheck0001";

function renderSection({
  api = createStubCustodianChecksApi(ME, 0, "sample", NOW),
  canChange = true,
  initialCheck = REVIEW,
}: { api?: CustodianChecksApi; canChange?: boolean; initialCheck?: string | null } = {}) {
  let keys = 0;
  render(<CustodianChecks api={api} canChange={canChange} newKey={() => `key-${++keys}`} today={() => "2026-11-14"} initialCheck={initialCheck} pollMs={60_000} />);
  return userEvent.setup();
}

const stub = (which: CustodianStubCase) => createStubCustodianChecksApi(ME, 0, which, NOW);

describe("custodian statement checks", () => {
  it("shows every line as read beside what was counted, and each difference", async () => {
    renderSection();
    const check = await screen.findByRole("article", { name: "Holdings at Fri, 13 Nov 2026" });
    const differences = within(check).getByRole("region", { name: "Differences" });
    expect(differences).toHaveTextContent("4 to explain, 4 without a note");
    expect(differences).toHaveTextContent("Amounts differ");
    expect(differences).toHaveTextContent("Not on the statement");
    expect(differences).toHaveTextContent("Not in Finvest's records");
    expect(differences).toHaveTextContent("Line could not be counted");
    const lines = within(check).getByRole("region", { name: "Lines as read" });
    expect(lines).toHaveTextContent("04/02/2027 800,000,000");
    expect(lines).toHaveTextContent("Read as 80,000,000 UGX");
    expect(within(check).getByRole("button", { name: "Send for sign-off" })).toBeDisabled();
  });

  it("corrects a misread line with a reason, explains the rest and sends it for sign-off", async () => {
    const api = stub("sample");
    const correct = vi.spyOn(api, "correctRow");
    const submit = vi.spyOn(api, "submit");
    const user = renderSection({ api });
    const check = await screen.findByRole("article", { name: "Holdings at Fri, 13 Nov 2026" });
    const buttons = within(check).getAllByRole("button", { name: "Correct this line" });
    await user.click(buttons[1]!);
    const form = within(check).getByRole("form", { name: "Correct line 2" });
    await user.type(within(form).getByLabelText("Face value (UGX)"), "800,000,000");
    await user.click(within(form).getByRole("button", { name: "Save correction" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("Say why");
    await user.type(within(form).getByLabelText("Reason"), "The reader dropped a zero");
    await user.click(within(form).getByRole("button", { name: "Save correction" }));
    expect(correct).toHaveBeenCalledWith(REVIEW, "-OdCustodianRow00002", {
      leftOut: false,
      faceMinor: 800_000_000,
      maturesOn: null,
      reason: "The reader dropped a zero",
    });
    expect(await screen.findByText("Saved. The comparison was worked out again.")).toBeInTheDocument();
    const differences = screen.getByRole("region", { name: "Differences" });
    expect(differences).toHaveTextContent("3 to explain, 3 without a note");

    for (const form of within(differences).getAllByRole("form")) {
      await user.type(within(form).getByLabelText("Why it differs"), "Explained");
      await user.click(within(form).getByRole("button", { name: "Save note" }));
      await screen.findByText("Note saved.");
    }
    expect(screen.getByRole("region", { name: "Differences" })).toHaveTextContent("3 to explain");
    await user.click(screen.getByRole("button", { name: "Send for sign-off" }));
    expect(submit).toHaveBeenCalledWith(expect.any(String), REVIEW, null);
    expect(await screen.findByText("Sent. A different person must now sign it off.")).toBeInTheDocument();
    expect(screen.getByText("Sent for sign-off. A different person must sign it off or send it back.")).toBeInTheDocument();
  });

  it("lets a second person sign off, and needs a note to send it back", async () => {
    const api = stub("sample");
    const signOff = vi.spyOn(api, "signOff");
    const user = renderSection({ api, initialCheck: WAITING });
    const check = await screen.findByRole("article", { name: /Holdings at/ });
    expect(check).toHaveTextContent("Corrected: The statement shows 800,000,000");
    await user.click(within(check).getByRole("button", { name: "Send back" }));
    expect(within(check).getByRole("alert")).toHaveTextContent("Say what needs another look.");
    await user.click(within(check).getByRole("button", { name: "Sign off as second person" }));
    expect(signOff).toHaveBeenCalledWith("key-1", WAITING, null);
    expect(await screen.findByText("Signed off.")).toBeInTheDocument();
  });

  it("says why a statement could not be read", async () => {
    renderSection({ initialCheck: UNREADABLE });
    expect(await screen.findByText(/could not be opened. Upload the statement again/)).toBeInTheDocument();
  });

  it("uploads a statement with its date", async () => {
    const api = stub("empty");
    const upload = vi.spyOn(api, "upload");
    const user = renderSection({ api, initialCheck: null });
    expect(await screen.findByText(/No custodian statement has been checked yet/)).toBeInTheDocument();
    const form = screen.getByRole("form", { name: "Upload a custodian statement" });
    const file = new File(["instrument,code"], "statement.csv", { type: "text/csv" });
    await user.type(within(form).getByLabelText("Holdings at"), "2026-11-13");
    await user.upload(within(form).getByLabelText("Statement file"), file);
    await user.click(within(form).getByRole("button", { name: "Upload and read" }));
    expect(upload).toHaveBeenCalledWith("key-1", "2026-11-13", file);
    expect(await screen.findByText(/Uploaded. The statement is being read/)).toBeInTheDocument();
  });

  it("says when reading is not set up, and offers no upload", async () => {
    renderSection({ api: stub("off"), initialCheck: null });
    expect(await screen.findByText(/Reading custodian statements is not set up/)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Upload a custodian statement" })).not.toBeInTheDocument();
  });

  it("offers nothing to change without the permission", async () => {
    renderSection({ canChange: false });
    const check = await screen.findByRole("article", { name: "Holdings at Fri, 13 Nov 2026" });
    expect(screen.queryByRole("form", { name: "Upload a custodian statement" })).not.toBeInTheDocument();
    expect(within(check).queryByRole("button", { name: "Correct this line" })).not.toBeInTheDocument();
  });

  it("words a failure to load", async () => {
    renderSection({ api: stub("error"), initialCheck: null });
    expect(await screen.findByText("Custodian checks could not be loaded")).toBeInTheDocument();
    expect(screen.getByText("We could not reach Finvest. Try again in a moment.")).toBeInTheDocument();
  });
});
