import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { BankTransferError, notConnectedBankTransfersApi, type BankTransfersApi } from "./api.ts";
import { daysApart, formatExact } from "./format.ts";
import { BankTransfersScreen } from "./index.tsx";
import { createStubBankTransfersApi } from "./stub.ts";

const ME = "sample-me";
const NOW = new Date("2026-11-13T09:00:00Z");

function renderScreen({
  api = createStubBankTransfersApi(ME, 0, "sample", () => NOW),
  canDecide = true,
  address = "/reconciliation",
}: { api?: BankTransfersApi; canDecide?: boolean; address?: string } = {}) {
  const keys: string[] = [];
  render(
    <MemoryRouter initialEntries={[address]}>
      <BankTransfersScreen
        api={api}
        canDecide={canDecide}
        newKey={() => {
          keys.push(`key-${keys.length + 1}`);
          return keys[keys.length - 1] as string;
        }}
      />
    </MemoryRouter>,
  );
  return { api, keys, user: userEvent.setup() };
}

const transfer = (amount: string) => screen.findByRole("article", { name: `Transfer of ${amount}` });
const queueItem = (amount: string) =>
  within(screen.getByRole("list", { name: "Bank transfers" })).getByRole("button", { name: new RegExp(amount) });

describe("bank transfers on the reconciliation screen", () => {
  it("lists what waits and opens the newest, with its receipt and its candidate lines", async () => {
    renderScreen();
    const card = await transfer("UGX 10,000,000");
    expect(screen.getByText("2 to match, 2 waiting for a second person.")).toBeInTheDocument();
    expect(within(card).getByRole("img", { name: "The client's receipt" })).toBeInTheDocument();
    expect(within(card).getByText("Centenary Bank")).toBeInTheDocument();
    // Nothing on the statement: the only way forward is to refuse, with "not on statement" chosen.
    expect(within(card).getByText(/No line on the imported bank statements/)).toBeInTheDocument();
    expect(within(card).getByLabelText("Why it cannot be matched")).toHaveValue("not_on_statement");
    expect(within(card).queryByRole("button", { name: "Match to this line" })).not.toBeInTheDocument();
  });

  it("matches a transfer to the line the person picks, and then waits for someone else", async () => {
    const { user, keys } = renderScreen();
    await transfer("UGX 10,000,000");
    await user.click(queueItem("UGX 2,500,000"));
    const card = await transfer("UGX 2,500,000");
    await user.click(within(card).getByRole("button", { name: "Match to this line" }));
    expect(within(card).getByRole("alert")).toHaveTextContent("Choose the statement line that is this transfer.");
    expect(keys).toEqual([]);

    await user.click(within(card).getByRole("radio", { name: /RTGS IN DAVID MUGISHA/ }));
    await user.click(within(card).getByRole("button", { name: "Match to this line" }));
    const matched = await transfer("UGX 2,500,000");
    expect(within(matched).getByRole("status")).toHaveTextContent("You matched this");
    expect(within(matched).getByText("RTGS IN DAVID MUGISHA FINVEST")).toBeInTheDocument();
    expect(within(matched).queryByRole("button", { name: "Approve as second person" })).not.toBeInTheDocument();
    expect(keys).toEqual(["key-1"]);
    expect(queueItem("UGX 2,500,000")).toHaveTextContent("Needs a second person");
  });

  it("lets a second person approve a match after seeing the line beside the receipt", async () => {
    const { user } = renderScreen({ address: "/reconciliation?transfer=-OdBank000000000003A" });
    const card = await transfer("UGX 5,000,000");
    expect(within(card).getByRole("status")).toHaveTextContent("Another member of staff matched this");
    const line = within(card).getByRole("region", { name: "Matched statement line" });
    expect(line).toHaveTextContent("TRANSFER FROM JOHN OKELLO DFCU");
    expect(line).toHaveTextContent("UGX 5,000,000");
    await user.click(within(card).getByRole("button", { name: "Approve as second person" }));
    const done = await transfer("UGX 5,000,000");
    expect(within(done).getByRole("status")).toHaveTextContent("Confirmed. The money is recorded as received");
    expect(within(screen.getByRole("list", { name: "Bank transfers" })).queryByRole("button", { name: /UGX 5,000,000/ })).toBeNull();
  });

  it("never offers approval to the person who matched it", async () => {
    renderScreen({ address: "/reconciliation?transfer=-OdBank000000000004A" });
    const card = await transfer("UGX 1,200,000");
    expect(within(card).getByRole("status")).toHaveTextContent("A second person has to approve it");
    expect(within(card).queryByRole("button")).toBeNull();
  });

  it("refuses a transfer with a reason and moves no money", async () => {
    const { user } = renderScreen({ address: "/reconciliation?transfer=-OdBank000000000001A" });
    const card = await transfer("UGX 2,500,000");
    await user.click(within(card).getByRole("button", { name: "It is not on the statement" }));
    await user.click(within(card).getByRole("button", { name: "Refuse the transfer" }));
    expect(within(card).getByRole("alert")).toHaveTextContent("Choose why it cannot be matched.");
    await user.selectOptions(within(card).getByLabelText("Why it cannot be matched"), "name_differs");
    await user.click(within(card).getByRole("button", { name: "Refuse the transfer" }));
    const refused = await transfer("UGX 2,500,000");
    expect(within(refused).getByRole("status")).toHaveTextContent(
      "Refused: sent from an account in another name. No money was recorded",
    );
  });

  it("shows transfers but no decisions to someone without the decide permission", async () => {
    renderScreen({ canDecide: false, address: "/reconciliation?transfer=-OdBank000000000003A" });
    const card = await transfer("UGX 5,000,000");
    expect(within(card).getByText(/need the bank transfers permission/)).toBeInTheDocument();
    expect(within(card).queryByRole("button")).toBeNull();
  });

  it("sends the same key again when an answer never arrived, and a new one after a refusal", async () => {
    const stub = createStubBankTransfersApi(ME, 0, "sample", () => NOW);
    let calls = 0;
    const seen: string[] = [];
    const api: BankTransfersApi = {
      ...stub,
      approve: (key, id) => {
        seen.push(key);
        calls += 1;
        if (calls === 1) return Promise.reject(new BankTransferError("unavailable"));
        if (calls === 2) return Promise.reject(new BankTransferError("same_person"));
        return stub.approve(key, id);
      },
    };
    const { user } = renderScreen({ api, address: "/reconciliation?transfer=-OdBank000000000003A" });
    const card = await transfer("UGX 5,000,000");
    const approve = () => user.click(within(card).getByRole("button", { name: "Approve as second person" }));
    await approve();
    expect(await within(card).findByRole("alert")).toHaveTextContent("We could not reach Finvest");
    await approve();
    expect(await within(card).findByRole("alert")).toHaveTextContent("A second person has to approve this");
    await approve();
    expect(seen).toEqual(["key-1", "key-1", "key-2"]);
  });

  it("says when there is nothing to check, and when the service cannot be reached", async () => {
    renderScreen({ api: createStubBankTransfersApi(ME, 0, "empty", () => NOW) });
    expect(await screen.findByText("Nothing to check")).toBeInTheDocument();
  });

  it("offers to try again when the list cannot be loaded", async () => {
    renderScreen({ api: notConnectedBankTransfersApi });
    expect(await screen.findByRole("alert")).toHaveTextContent("Bank transfers are not connected to the Finvest service yet.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("bank transfer figures", () => {
  it("shows amounts exactly", () => {
    expect(formatExact({ minor: 2_500_000, currency: "UGX" })).toBe("UGX 2,500,000");
    expect(formatExact({ minor: 362_000_05, currency: "USD" })).toBe("$362,000.05");
  });

  it("says how far a statement line is from the day sent", () => {
    expect(daysApart("2026-11-12", "2026-11-12")).toBe("Same day as sent");
    expect(daysApart("2026-11-12", "2026-11-13")).toBe("1 day after sent");
    expect(daysApart("2026-11-12", "2026-11-15")).toBe("3 days after sent");
    expect(daysApart("2026-11-12", "2026-11-11")).toBe("1 day before the day sent");
  });
});
