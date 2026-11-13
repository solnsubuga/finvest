import { act, fireEvent, screen } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { DEMO_PASSWORD } from "@/auth/stubAuthClient";
import { PaymentError } from "@/payments/types";

const PDF = { uri: "file:///receipt.pdf", name: "transfer-receipt.pdf", mimeType: "application/pdf", size: 219_136 };

/** The demo's clock, moved by hand so each state can be looked at. */
const mockClock = { ms: Date.parse("2026-11-13T12:31:00Z") };
const mockPay = jest.fn();
const mockSubmit = jest.fn();
/** The paying number is the account's own, so tests choose the demo's outcome here. */
const mockPicked: { file: unknown } = { file: null };
const mockScenario = { code: "00" };

jest.mock("@/auth/stubAuthClient", () => {
  const actual = jest.requireActual("@/auth/stubAuthClient");
  return {
    ...actual,
    createStubAuthClient: () => actual.createStubAuthClient({ delayMs: 0 }),
  };
});

jest.mock("@/payments/stubPaymentsClient", () => {
  const actual = jest.requireActual("@/payments/stubPaymentsClient");
  return {
    ...actual,
    createStubPaymentsClient: () => {
      const client = actual.createStubPaymentsClient({
        delayMs: 0,
        now: () => mockClock.ms,
        scenarioFor: () => mockScenario.code,
      });
      return {
        ...client,
        submitBankReceipt: async (submission: unknown) => {
          mockSubmit(submission);
          return client.submitBankReceipt(submission);
        },
        payWithMobileMoney: async (request: unknown) => {
          mockPay(request);
          return client.payWithMobileMoney(request);
        },
      };
    },
  };
});

// The preset's mock of the native module returns nothing. Tests need real, distinct keys.
jest.mock("expo-crypto", () => ({
  randomUUID: () => jest.requireActual("node:crypto").randomUUID(),
}));

jest.mock("@/payments/pickReceipt", () => ({
  chooseReceiptFile: async () => mockPicked.file,
  photographReceipt: async () => mockPicked.file,
}));

jest.mock("expo-clipboard", () => ({ setStringAsync: jest.fn(async () => true) }));

jest.mock("@/payments/pollInterval", () => ({ POLL_MS: 25 }));

jest.mock("expo-font", () => ({
  ...jest.requireActual("expo-font"),
  useFonts: () => [true, null],
}));

async function openPayScreen() {
  await renderRouter("./src/app", { initialUrl: "/" });
  await fireEvent.press(await screen.findByText("Log in"));
  await fireEvent.changeText(await screen.findByTestId("phone-input"), "772123456");
  await fireEvent.changeText(screen.getByTestId("password-input"), DEMO_PASSWORD);
  await fireEvent.press(screen.getByTestId("login-submit"));
  await fireEvent.press(await screen.findByTestId("use-passkey"));
  await screen.findByText("Welcome, Amara");
  await fireEvent.press(screen.getByLabelText("Invest"));
  await fireEvent.press(await screen.findByTestId("demo-pay"));
  await screen.findByText("Pay UGX 1,000,000");
}

async function advance(ms: number) {
  await act(async () => {
    mockClock.ms += ms;
  });
}

beforeEach(() => {
  mockPay.mockClear();
  mockSubmit.mockClear();
  mockPicked.file = PDF;
  mockScenario.code = "00";
});

async function openUpload() {
  await openPayScreen();
  await fireEvent.press(screen.getByTestId("pay-by-bank"));
  await screen.findByText("Pay by bank transfer");
  await screen.findByTestId("bank-sent");
  return screen;
}

async function fill(number = "TXN123456") {
  await fireEvent.press(screen.getByTestId("receipt-choose"));
  await screen.findByTestId("receipt-file");
  await fireEvent.changeText(screen.getByTestId("receipt-amount"), "1000000");
  await fireEvent.changeText(screen.getByTestId("receipt-day"), "13/11/2026");
  await fireEvent.changeText(screen.getByTestId("receipt-bank"), "Demo Bank");
  await fireEvent.changeText(screen.getByTestId("receipt-number"), number);
}

describe("paying for an investment by bank transfer", () => {
  beforeAll(() => {
    jest.spyOn(Date, "now").mockImplementation(() => mockClock.ms);
  });
  afterAll(() => {
    jest.restoreAllMocks();
  });

  it("shows where to send the money, the reference, and the own-name rule", async () => {
    await openUpload();
    expect(screen.getByText(/Send UGX 1,000,000 to the account below/)).toBeTruthy();
    expect(screen.getByText("Demo Bank")).toBeTruthy();
    expect(screen.getByText("0000 0000 0000")).toBeTruthy();
    expect(screen.getByText(/Put 1001004827 as the reference/)).toBeTruthy();
    expect(screen.getByText(/Send from a bank account in your own name/)).toBeTruthy();
    await fireEvent.press(screen.getByLabelText("Copy account number"));
    await screen.findByText("Copied");
  });

  it("goes from the receipt to confirmed once it is matched", async () => {
    await openUpload();
    await fireEvent.press(screen.getByTestId("bank-sent"));
    await screen.findByText("Upload your receipt");
    await fill();
    expect(screen.getByText("transfer-receipt.pdf")).toBeTruthy();
    expect(screen.getByText("214 KB, ready to send")).toBeTruthy();
    expect(screen.getByTestId("receipt-amount").props.value).toBe("1,000,000");
    await fireEvent.press(screen.getByTestId("receipt-submit"));

    await screen.findByText("We have your receipt");
    expect(screen.getByText("Matching it to our bank statement")).toBeTruthy();
    expect(mockSubmit.mock.calls[0][0]).toMatchObject({
      amountSent: { currency: "UGX", minor: 1_000_000 },
      sentOn: "2026-11-13",
      bankName: "Demo Bank",
      transactionNumber: "TXN123456",
      file: { mimeType: "application/pdf", sizeBytes: 219_136 },
    });

    await advance(10_000);
    await screen.findByText("Completed");
    expect(screen.getByText("Bank transfer")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("transfer-done"));
    await screen.findByText("Welcome, Amara");
  });

  it("sends nothing until the receipt and every detail are there", async () => {
    await openUpload();
    await fireEvent.press(screen.getByTestId("bank-sent"));
    await screen.findByText("Upload your receipt");
    await fireEvent.press(screen.getByTestId("receipt-submit"));
    await screen.findByText("Add your receipt.");
    expect(screen.getByText("Enter the amount you sent, in shillings.")).toBeTruthy();
    expect(screen.getByText(/Enter the day you sent it/)).toBeTruthy();
    expect(screen.getByText("Enter the bank you sent from.")).toBeTruthy();
    expect(screen.getByText("Enter the transaction number on the receipt.")).toBeTruthy();

    await fill();
    await fireEvent.changeText(screen.getByTestId("receipt-day"), "14/11/2026");
    await fireEvent.press(screen.getByTestId("receipt-submit"));
    expect(screen.getByText(/It cannot be in the future/)).toBeTruthy();
    expect(mockSubmit).not.toHaveBeenCalled();
  });

  it("refuses a file that is not a receipt we can take", async () => {
    await openUpload();
    await fireEvent.press(screen.getByTestId("bank-sent"));
    await screen.findByText("Upload your receipt");
    mockPicked.file = { uri: "file:///clip.mp4", name: "clip.mp4", mimeType: "video/mp4", size: 900 };
    await fireEvent.press(screen.getByTestId("receipt-choose"));
    await screen.findByText("That file type is not accepted. Use a JPG, PNG or PDF.");
    expect(screen.queryByTestId("receipt-file")).toBeNull();
    mockPicked.file = { ...PDF, size: 11 * 1024 * 1024 };
    await fireEvent.press(screen.getByTestId("receipt-choose"));
    await screen.findByText("That file is larger than 10 MB. Choose a smaller one.");
  });

  it("says why a receipt was not matched and takes another", async () => {
    await openUpload();
    await fireEvent.press(screen.getByTestId("bank-sent"));
    await screen.findByText("Upload your receipt");
    await fill("TXN000001");
    await fireEvent.press(screen.getByTestId("receipt-submit"));
    await screen.findByText("We have your receipt");

    await advance(10_000);
    await screen.findByText("We could not match your receipt");
    expect(
      screen.getByText("The amount on the receipt is different from the amount we received."),
    ).toBeTruthy();
    expect(screen.getByText(/No investment was started/)).toBeTruthy();

    await fireEvent.press(screen.getByTestId("transfer-replace"));
    await screen.findByText("Upload your receipt");
    await fill("TXN123456");
    await fireEvent.press(screen.getByTestId("receipt-submit"));
    await screen.findByText("We have your receipt");
    const keys = new Set(mockSubmit.mock.calls.map(([submission]) => submission.idempotencyKey));
    expect(keys.size).toBe(2);
  });

  it("sends the same receipt under the same key after a lost try", async () => {
    await openUpload();
    await fireEvent.press(screen.getByTestId("bank-sent"));
    await screen.findByText("Upload your receipt");
    await fill();
    mockSubmit.mockImplementationOnce(() => {
      throw new PaymentError("unavailable");
    });
    await fireEvent.press(screen.getByTestId("receipt-submit"));
    await screen.findByText(/Your first try may already be with us/);
    // The receipt and the details can no longer be changed.
    expect(screen.queryByTestId("receipt-choose")).toBeNull();
    expect(screen.getByTestId("receipt-amount").props.editable).toBe(false);

    await fireEvent.press(screen.getByTestId("receipt-submit"));
    await screen.findByText("We have your receipt");
    expect(mockSubmit).toHaveBeenCalledTimes(2);
    expect(mockSubmit.mock.calls[1][0]).toEqual(mockSubmit.mock.calls[0][0]);
  });
});
