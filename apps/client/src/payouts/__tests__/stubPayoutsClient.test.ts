import { canProveAgain, isPayable } from "../format";
import { createStubPayoutsClient, DEMO_PAYOUT_CODE, RETURNED_DEMO_PAYOUT } from "../stubPayoutsClient";
import { PayoutsError } from "../types";

const NOW = Date.parse("2026-11-13T09:00:00Z");
const client = (sample: "default" | "empty" = "empty") => createStubPayoutsClient({ sample, delayMs: 0, now: () => NOW });

async function code(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    return error instanceof PayoutsError ? error.code : "other";
  }
  return "none";
}

const mobile = { kind: "mobile_money" as const, phone: "0772 555 010", holderName: "Amara Nakato" };
const bank = { kind: "bank" as const, bankCode: "stanbic", accountNumber: "9030 0123 4567", holderName: "Amara Nakato" };

it("walks a bank account from the code to the team's check", async () => {
  const api = client();
  const added = await api.addAccount(bank);
  expect(added).toMatchObject({ status: "awaiting_code", account: "•••• 4567", bankCode: "stanbic" });
  // No code texted yet, so none can be right.
  expect(await code(api.verifyCode(added.id, DEMO_PAYOUT_CODE))).toBe("code_expired");
  await api.sendCode(added.id);
  expect(await code(api.verifyCode(added.id, "000000"))).toBe("invalid_code");
  expect((await api.verifyCode(added.id, DEMO_PAYOUT_CODE)).status).toBe("awaiting_proof");
  expect(await code(api.sendCode(added.id))).toBe("moved_on");
  const file = { uri: "x", name: "s.pdf", mimeType: "application/pdf" as const, sizeBytes: 10 };
  expect(await api.uploadProof(added.id, "bank_statement", file)).toMatchObject({ status: "in_review", proofKind: "bank_statement" });
  expect(await code(api.uploadProof(added.id, "bank_statement", file))).toBe("moved_on");
});

it("stops trying a code after five wrong ones", async () => {
  const api = client();
  const added = await api.addAccount(mobile);
  await api.sendCode(added.id);
  for (let i = 0; i < 5; i += 1) expect(await code(api.verifyCode(added.id, "000000"))).toBe("invalid_code");
  expect(await code(api.verifyCode(added.id, DEMO_PAYOUT_CODE))).toBe("code_expired");
});

it("refuses what the backend refuses", async () => {
  const api = client();
  expect(await code(api.addAccount({ ...mobile, phone: "0612 345 678" }))).toBe("phone_network_unsupported");
  expect(await code(api.addAccount({ ...mobile, phone: "12" }))).toBe("phone_invalid");
  expect(await code(api.addAccount({ ...bank, bankCode: "nowhere" }))).toBe("bank_invalid");
  expect(await code(api.addAccount({ ...bank, accountNumber: "123" }))).toBe("account_number_invalid");
  expect(await code(api.addAccount({ ...mobile, holderName: "  " }))).toBe("holder_name_invalid");
  const added = await api.addAccount(mobile);
  expect(await code(api.addAccount({ ...mobile, phone: "+256 772 555 010" }))).toBe("already_saved");
  const file = { uri: "x", name: "s.pdf", mimeType: "application/pdf" as const, sizeBytes: 10 };
  // Proof is for bank accounts only.
  expect(await code(api.uploadProof(added.id, "bank_card", file))).toBe("moved_on");
});

it("keeps at most five accounts waiting for their code", async () => {
  const api = client();
  for (let i = 0; i < 5; i += 1) await api.addAccount({ ...mobile, phone: `077255501${i}` });
  expect(await code(api.addAccount({ ...mobile, phone: "0772555019" }))).toBe("too_many_unconfirmed");
});

it("removes an account for good", async () => {
  const api = client("default");
  await api.removeAccount("demo-account-centenary");
  expect((await api.listAccounts()).map((account) => account.id)).not.toContain("demo-account-centenary");
  expect(await code(api.getAccount("demo-account-centenary"))).toBe("not_found");
});

it("seeds the demo with a payable account, one in its first day and one to prove again", async () => {
  const accounts = await client("default").listAccounts();
  const byId = Object.fromEntries(accounts.map((account) => [account.id, account]));
  expect(isPayable(byId["demo-account-mtn"]!, NOW)).toBe(true);
  expect(isPayable(byId["demo-account-bank"]!, NOW)).toBe(false);
  expect(canProveAgain(byId["demo-account-centenary"]!.rejectionReason)).toBe(true);
});

it("never lets a different name or suspected fraud be proved again", () => {
  expect(canProveAgain("name_different")).toBe(false);
  expect(canProveAgain("suspected_fraud")).toBe(false);
  expect(canProveAgain("proof_too_old")).toBe(true);
});

it("fails every read when offline, to show the error states", async () => {
  const api = createStubPayoutsClient({ offline: true, delayMs: 0 });
  expect(await code(api.listAccounts())).toBe("unavailable");
  expect(await code(api.listPayouts())).toBe("unavailable");
});

describe("the demo bank list", () => {
  it("lists the banks and names the demo accounts by them", async () => {
    const client = createStubPayoutsClient({ delayMs: 0 });
    const banks = await client.listBanks();
    expect(banks.map((bank) => bank.code)).toEqual(expect.arrayContaining(["stanbic", "centenary"]));
    const accounts = await client.listAccounts();
    expect(accounts.find((account) => account.id === "demo-account-bank")?.bankName).toBe("Stanbic Bank");
    expect(accounts.find((account) => account.id === "demo-account-mtn")?.bankName).toBeNull();
  });
});

it("sends a payout that came back again once, to a ready account", async () => {
  const api = client("default");
  const returned = await api.getPayout(RETURNED_DEMO_PAYOUT);
  expect(returned.returnedAt).not.toBeNull();
  // Not one that came back.
  expect(await code(api.resend("demo-payout-1", "demo-account-mtn"))).toBe("not_returned");
  // Still in its first day, and not approved.
  expect(await code(api.resend(RETURNED_DEMO_PAYOUT, "demo-account-bank"))).toBe("account_not_ready");
  expect(await code(api.resend(RETURNED_DEMO_PAYOUT, "demo-account-centenary"))).toBe("account_not_ready");

  const sent = await api.resend(RETURNED_DEMO_PAYOUT, "demo-account-mtn");
  expect(sent).toMatchObject({ destinationId: "demo-account-mtn", status: "processing", amount: returned.amount, returnedAt: null });
  // Asking again with the same account is the same payout; another account is refused.
  expect(await api.resend(RETURNED_DEMO_PAYOUT, "demo-account-mtn")).toEqual(sent);
  expect(await code(api.resend(RETURNED_DEMO_PAYOUT, "demo-account-bank"))).toBe("already_resent");
  expect((await api.listPayouts())[0]).toEqual(sent);
  expect((await api.getPayout(RETURNED_DEMO_PAYOUT)).resentAs).toBe(sent.id);
});

it("lets a new account take a payout that came back once its first day has passed", async () => {
  let at = NOW;
  const api = createStubPayoutsClient({ sample: "default", delayMs: 0, now: () => at });
  at = NOW + 2 * 24 * 60 * 60 * 1000;
  // The bank account is past its first day now, so it can take it.
  expect((await api.resend(RETURNED_DEMO_PAYOUT, "demo-account-bank")).kind).toBe("bank");
});
