import { act, fireEvent, screen } from "@testing-library/react-native";
import { router } from "expo-router";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { WARM_ROUTER_MS, warmRouter } from "@/testing/warmRouter";

const mockPayOut = jest.fn();
const mockChoose = jest.fn();
const mockInvesting: { maturedWaiting: boolean; failPayOut: string | null } = { maturedWaiting: false, failPayOut: null };

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return { ...actual, createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }) };
});

const mockPayoutsSample: { nameCheck: string | undefined; askedElsewhere: boolean } = {
  nameCheck: undefined,
  askedElsewhere: false,
};
jest.mock("@/payouts/stubPayoutsClient", () => {
  const actual = jest.requireActual("@/payouts/stubPayoutsClient");
  return {
    ...actual,
    createStubPayoutsClient: () => {
      const client = actual.createStubPayoutsClient({ delayMs: 0, sample: "default", nameCheck: mockPayoutsSample.nameCheck });
      return {
        ...client,
        // As if the client had already asked again on another device: the number is checking,
        // and this ask is refused for that.
        checkNameAgain: async (accountId: string) => {
          if (!mockPayoutsSample.askedElsewhere) return client.checkNameAgain(accountId);
          await client.checkNameAgain(accountId);
          const { PayoutsError } = jest.requireActual("@/payouts/types");
          throw new PayoutsError("name_check_in_progress");
        },
      };
    },
  };
});

jest.mock("@/payouts/timing", () => ({ NAME_DECISION_READ_AGAIN_MS: 20 }));

jest.mock("@/investing/stubInvestingClient", () => {
  const actual = jest.requireActual("@/investing/stubInvestingClient");
  return {
    ...actual,
    createStubInvestingClient: () => {
      const client = actual.createStubInvestingClient({ delayMs: 0, maturedWaiting: mockInvesting.maturedWaiting });
      return {
        ...client,
        chooseAtMaturity: async (choice: unknown) => {
          mockChoose(choice);
          return client.chooseAtMaturity(choice);
        },
        payOutMatured: async (request: unknown) => {
          mockPayOut(request);
          const code = mockInvesting.failPayOut;
          if (code) {
            mockInvesting.failPayOut = null;
            const { InvestingError } = jest.requireActual("@/investing/types");
            throw new InvestingError(code);
          }
          return client.payOutMatured(request);
        },
      };
    },
  };
});

const mockPicked = { uri: "file:///statement.pdf", name: "statement.pdf", mimeType: "application/pdf", size: 120_000 };
jest.mock("@/payments/pickReceipt", () => ({
  chooseReceiptFile: async () => mockPicked,
  photographReceipt: async () => null,
}));

jest.mock("expo-crypto", () => ({
  randomUUID: () => jest.requireActual("node:crypto").randomUUID(),
}));

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

async function signIn() {
  await renderRouter("./src/app", { initialUrl: "/" });
  await fireEvent.press(await screen.findByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "772123456");
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await fireEvent.press(await screen.findByTestId("use-passkey"));
  await screen.findByText("Welcome, Amara");
}

async function open(path: string) {
  await act(async () => {
    router.push(path as never);
  });
}

beforeAll(warmRouter, WARM_ROUTER_MS);

beforeEach(() => {
  mockPayOut.mockClear();
  mockChoose.mockClear();
  mockInvesting.maturedWaiting = false;
  mockInvesting.failPayOut = null;
  mockPayoutsSample.nameCheck = undefined;
  mockPayoutsSample.askedElsewhere = false;
});

describe("payout accounts", () => {
  it("are reached from the profile", async () => {
    await signIn();
    await open("/profile");
    await fireEvent.press(await screen.findByTestId("open-payout-accounts"));
    expect(await screen.findByTestId("payout-account-demo-account-mtn")).toBeTruthy();
    expect(screen.getByLabelText(/^MTN \+256 77• ••• 456\. Ready$/)).toBeTruthy();
    expect(screen.getByLabelText(/^Stanbic Bank •••• 4821\. Ready soon$/)).toBeTruthy();
  });

  it("adds a bank account, takes the texted code and sends the proof", async () => {
    await signIn();
    await open("/payouts/add");
    await fireEvent.press(await screen.findByTestId("payout-kind-bank"));
    // Nothing chosen or typed: refused on the form, nothing is sent.
    await fireEvent.press(screen.getByTestId("payout-account-save"));
    expect(await screen.findByText("Choose your bank from the list.")).toBeTruthy();
    expect(screen.getByText("Enter the name the account is in.")).toBeTruthy();

    await fireEvent.press(screen.getByTestId("bank-dfcu"));
    await fireEvent.changeText(screen.getByTestId("account-number-input"), "0123 4567 89");
    await fireEvent.changeText(screen.getByTestId("holder-name-input"), "Amara Nakato");
    await fireEvent.press(screen.getByTestId("payout-account-save"));

    expect(await screen.findByText("We texted a code to your own mobile number")).toBeTruthy();
    await fireEvent.changeText(screen.getByTestId("code-input"), "000000");
    await fireEvent.press(screen.getByTestId("payout-code-submit"));
    expect(await screen.findByText("That code is not right. Check it and try again.")).toBeTruthy();
    await fireEvent.changeText(screen.getByTestId("code-input"), "123456");
    await fireEvent.press(screen.getByTestId("payout-code-submit"));

    expect(await screen.findByText("Send proof the account is yours")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("proof-bank_letter"));
    await fireEvent.press(screen.getByText("Choose a file"));
    expect(await screen.findByText("statement.pdf")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("proof-upload"));
    expect(await screen.findByText(/Our team is checking your proof against the name on your ID/)).toBeTruthy();
  });

  it("shows the name being compared after asking again with the ID approved, then where it went", async () => {
    mockPayoutsSample.nameCheck = "waiting_for_id";
    await signIn();
    await open("/payouts/demo-account-airtel");
    expect(await screen.findByTestId("name-check-waiting-for-id")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("name-check-again"));
    expect(await screen.findByTestId("name-check-comparing")).toBeTruthy();
    expect(await screen.findByText(/Our team is checking your proof against the name on your ID/)).toBeTruthy();
    expect(screen.queryByTestId("name-check-again")).toBeNull();
  });

  it("reloads the account when asking again finds the screen out of date", async () => {
    mockPayoutsSample.nameCheck = "not_answered";
    mockPayoutsSample.askedElsewhere = true;
    await signIn();
    await open("/payouts/demo-account-airtel");
    await fireEvent.press(await screen.findByTestId("name-check-again"));
    expect(await screen.findByText(/We are checking the name the network holds/)).toBeTruthy();
    expect(screen.queryByTestId("name-check-again")).toBeNull();
  });

  it("offers new proof after a fixable refusal, and removes an account after asking", async () => {
    await signIn();
    await open("/payouts/demo-account-centenary");
    expect(await screen.findByText("The statement was more than three months old. Send a newer one.")).toBeTruthy();
    expect(screen.getByText("Send new proof")).toBeTruthy();

    await fireEvent.press(screen.getByText("Remove this account"));
    expect(await screen.findByTestId("remove-confirm")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("remove-account"));
    expect(await screen.findByTestId("payout-account-demo-account-mtn")).toBeTruthy();
    expect(screen.queryByTestId("payout-account-demo-account-centenary")).toBeNull();
  });

  it("lists payouts with the limits and opens one", async () => {
    await signIn();
    await open("/payouts/history");
    expect(await screen.findByText("UGX 5,000,000")).toBeTruthy();
    await fireEvent.press(await screen.findByTestId("payout-demo-payout-2"));
    expect(await screen.findByTestId("payout-amount")).toHaveTextContent("UGX 512,400");
    expect(screen.getAllByText("On its way").length).toBeGreaterThan(0);
  });

  it("sends a payout that came back again and opens the new one", async () => {
    await signIn();
    await open("/payouts/history");
    await fireEvent.press(await screen.findByTestId("payout-demo-payout-returned"));
    expect(await screen.findByText("Came back to us")).toBeTruthy();
    expect(screen.getByText("A bank account you removed")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("payout-resend"));
    expect(await screen.findByTestId("resend-amount")).toHaveTextContent("UGX 2,150,000");
    // The new bank account is still in its first day, so the number is chosen for it.
    expect(screen.getByTestId("account-demo-account-bank")).toBeDisabled();
    expect(screen.getByTestId("account-demo-account-mtn")).toBeChecked();
    await fireEvent.press(screen.getByTestId("resend-send"));
    expect(await screen.findByTestId("payout-amount")).toHaveTextContent("UGX 2,150,000");
    expect(screen.getAllByText("On its way").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("payout-resend")).toBeNull();

    // The one that came back now says it was sent again, and points at the new payout.
    await open("/payouts/payout/demo-payout-returned");
    expect(await screen.findByText("This payout came back to us, and you sent it again. The new payout has the money now.")).toBeTruthy();
    expect(screen.queryByTestId("payout-resend")).toBeNull();
    await fireEvent.press(screen.getByTestId("payout-resent-as"));
    expect(await screen.findByText("MTN +256 77• ••• 456")).toBeTruthy();
  });
});

describe("the maturity choice", () => {
  it("changes a roll over to a pay out to a chosen account", async () => {
    await signIn();
    await open("/portfolio");
    await fireEvent.press(await screen.findByLabelText(/^91 Day Treasury Bill, Running/));
    await fireEvent.press(await screen.findByTestId("change-maturity"));
    await fireEvent.press(await screen.findByTestId("maturity-pay_out"));
    // An account in its first day can be chosen for a maturity later on.
    await fireEvent.press(screen.getByTestId("account-demo-account-bank"));
    await fireEvent.press(screen.getByTestId("maturity-save"));
    expect(await screen.findByText("Pay out")).toBeTruthy();
    expect(mockChoose).toHaveBeenCalledWith(
      expect.objectContaining({ atMaturity: "pay_out", destinationId: "demo-account-bank" }),
    );
  });

  it("sends matured money to a ready account, and asks the same again after a lost answer", async () => {
    mockInvesting.maturedWaiting = true;
    await signIn();
    await open("/portfolio");
    await fireEvent.press(await screen.findByLabelText(/^91 Day Treasury Bill, Matured/));
    expect(await screen.findByText("Your money is ready. Choose where to be paid and we will send it.")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("choose-pay-out"));
    await screen.findByTestId("pay-out-amount");
    expect(screen.getByTestId("account-demo-account-bank")).toBeDisabled();

    mockInvesting.failPayOut = "unavailable";
    await fireEvent.press(screen.getByTestId("pay-out-send"));
    expect(await screen.findByText(/so you will not be paid twice/)).toBeTruthy();
    await fireEvent.press(screen.getByTestId("pay-out-send"));
    expect(await screen.findByTestId("open-investment-payout")).toBeTruthy();
    expect(mockPayOut.mock.calls.map((call) => call[0].destinationId)).toEqual(["demo-account-mtn", "demo-account-mtn"]);
  });
});
