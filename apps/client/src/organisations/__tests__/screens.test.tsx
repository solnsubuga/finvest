import { fireEvent, render, screen, waitFor } from "@testing-library/react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import OrganisationScreen from "@/app/account/organisations/[organisationId]";
import OrganisationDetails from "@/app/account/organisations/details";
import Organisations from "@/app/account/organisations/index";
import NewOrganisation from "@/app/account/organisations/new";
import { AuthProvider } from "@/auth/AuthProvider";
import { IdentityProvider } from "@/identity/IdentityProvider";
import { IdentityCheck, IdentityClient } from "@/identity/types";
import { OrganisationsProvider } from "@/organisations/OrganisationsProvider";
import { createStubOrganisationsClient } from "@/organisations/stubOrganisationsClient";
import { OrganisationsClient } from "@/organisations/types";

const mockRouter = { push: jest.fn(), replace: jest.fn(), navigate: jest.fn(), back: jest.fn(), canGoBack: () => false };
let mockParams: Record<string, string> = {};
let mockPicked: { uri: string; name: string; mimeType: string; size: number } | null = null;

jest.mock("expo-router", () => {
  const { useEffect } = jest.requireActual("react");
  return {
    ...jest.requireActual("expo-router"),
    useRouter: () => mockRouter,
    useLocalSearchParams: () => mockParams,
    useFocusEffect: (effect: () => void | (() => void)) => useEffect(effect, [effect]),
    Stack: { Screen: () => null },
  };
});

jest.mock("@/payments/pickReceipt", () => ({
  chooseReceiptFile: async () => mockPicked,
  photographReceipt: async () => null,
}));

function identity(status: IdentityCheck["status"] = "approved"): IdentityClient {
  return {
    getCheck: async () => ({ status, accountNumber: status === "approved" ? "1001004827" : null }) as IdentityCheck,
  } as unknown as IdentityClient;
}

async function show(node: React.ReactElement, client: OrganisationsClient, check = identity()) {
  await render(
    <SafeAreaProvider
      initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}
    >
      <AuthProvider>
        <IdentityProvider client={check}>
          <OrganisationsProvider client={client}>{node}</OrganisationsProvider>
        </IdentityProvider>
      </AuthProvider>
    </SafeAreaProvider>,
  );
}

const seeded = () => createStubOrganisationsClient("+256772123456");

beforeEach(() => {
  jest.clearAllMocks();
  mockParams = {};
  mockPicked = null;
});

describe("the company and group accounts list", () => {
  it("shows the invitations apart from the client's own", async () => {
    await show(<Organisations />, seeded());
    expect((await screen.findAllByText("Asked to sign")).length).toBe(2);
    expect(screen.getByText("Nakato Traders Ltd")).toBeTruthy();
    expect(screen.getByText("Brian K. asked you")).toBeTruthy();
    expect(screen.getByText("Kira Road Savings Group")).toBeTruthy();
  });

  it("says so when there are none", async () => {
    await show(<Organisations />, createStubOrganisationsClient("+256700000001"));
    expect(await screen.findByTestId("organisations-empty")).toBeTruthy();
    expect(screen.getByText("Open a company or group account")).toBeTruthy();
  });

  it("offers to try again when Finvest cannot be reached", async () => {
    await show(<Organisations />, createStubOrganisationsClient("+256700000099"));
    expect(await screen.findByText(/could not reach Finvest/)).toBeTruthy();
  });
});

describe("opening an account", () => {
  it("asks for a company or group, and not until the client's own check is approved", async () => {
    await show(<NewOrganisation />, seeded(), identity("submitted"));
    expect(await screen.findByText(/Finish your own ID check first/)).toBeTruthy();
    await fireEvent.press(screen.getByTestId("organisation-kind-company"));
    expect(screen.getByTestId("organisation-kind-continue").props.accessibilityState?.disabled).toBe(true);
  });

  it("goes on with the kind chosen", async () => {
    await show(<NewOrganisation />, seeded());
    expect(await screen.findByText(/You already have this one, 1001 004 827/)).toBeTruthy();
    await fireEvent.press(screen.getByTestId("organisation-kind-group"));
    await fireEvent.press(screen.getByTestId("organisation-kind-continue"));
    expect(mockRouter.push).toHaveBeenCalledWith({ pathname: "/account/organisations/details", params: { kind: "group" } });
  });

  it("refuses a name with a web address and a company without its number", async () => {
    mockParams = { kind: "company" };
    await show(<OrganisationDetails />, seeded());
    await fireEvent.changeText(screen.getByTestId("organisation-name"), "Visit finvest-help.com");
    await fireEvent.changeText(screen.getByTestId("organisation-role"), "Director");
    await fireEvent.press(screen.getByTestId("organisation-apply"));
    expect(await screen.findByText(/no web address or phone number/)).toBeTruthy();
    expect(screen.getByText("A company needs its registration number.")).toBeTruthy();
    expect(mockRouter.replace).not.toHaveBeenCalled();
  });

  it("applies and opens the new application", async () => {
    mockParams = { kind: "company" };
    const client = createStubOrganisationsClient("+256700000001");
    await show(<OrganisationDetails />, client);
    await fireEvent.changeText(screen.getByTestId("organisation-name"), "Mukono  Hardware (U) Ltd");
    await fireEvent.changeText(screen.getByTestId("organisation-registration"), "80020009876543");
    await fireEvent.changeText(screen.getByTestId("organisation-role"), "Director");
    await fireEvent.press(screen.getByTestId("organisation-apply"));
    await waitFor(() => expect(mockRouter.replace).toHaveBeenCalled());
    const [made] = await client.list();
    expect(made).toMatchObject({ name: "Mukono Hardware (U) Ltd", kind: "company", approvalsNeeded: 1, status: "draft" });
  });
});

describe("one application", () => {
  it("lists what is missing and does not send it yet", async () => {
    mockParams = { organisationId: "org-kira-road" };
    await show(<OrganisationScreen />, seeded());
    expect(await screen.findByText("Kira Road Savings Group")).toBeTruthy();
    expect(screen.getByText("Someone you asked has not answered yet.")).toBeTruthy();
    expect(screen.getByText("A document is still missing.")).toBeTruthy();
    expect(screen.getByText("Any 2 of 3 signatories")).toBeTruthy();
    expect(screen.getByText("All 3 signatories")).toBeTruthy();
    expect(screen.getByTestId("organisation-submit").props.accessibilityState?.disabled).toBe(true);
  });

  it("adds a signatory by number, and explains one Finvest cannot find", async () => {
    mockParams = { organisationId: "org-kira-road" };
    await show(<OrganisationScreen />, seeded());
    await fireEvent.press(await screen.findByTestId("signatory-add"));
    await fireEvent.changeText(screen.getByTestId("signatory-to"), "0700 000 001");
    await fireEvent.changeText(screen.getByTestId("signatory-role"), "Member");
    await fireEvent.press(screen.getByTestId("signatory-ask"));
    expect(await screen.findByText(/could not find an approved Finvest client/)).toBeTruthy();
    await fireEvent.changeText(screen.getByTestId("signatory-to"), "1001 004 903");
    await fireEvent.press(screen.getByTestId("signatory-ask"));
    expect(await screen.findByText("Brian K. will be asked in the app to join or decline.")).toBeTruthy();
    expect(screen.getByText("Any 2 of 4 signatories")).toBeTruthy();
  });

  it("takes the waiting person off, adds the missing paper and sends it", async () => {
    mockParams = { organisationId: "org-kira-road" };
    const client = seeded();
    await show(<OrganisationScreen />, client);
    await fireEvent.press(await screen.findByTestId("signatory-remove-2"));
    expect(await screen.findByText("Sarah A. is off the list.")).toBeTruthy();
    mockPicked = { uri: "file:///minutes.pdf", name: "minutes.pdf", mimeType: "application/pdf", size: 120_000 };
    await fireEvent.press(screen.getByTestId("document-send-group_resolution"));
    expect(await screen.findByText("Everything is ready to send.")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("organisation-submit"));
    expect(await screen.findByText(/A person at Finvest checks it/)).toBeTruthy();
    expect((await client.get("org-kira-road")).status).toBe("submitted");
  });

  it("refuses a file of a type Finvest does not take, before sending it", async () => {
    mockParams = { organisationId: "org-kira-road" };
    await show(<OrganisationScreen />, seeded());
    mockPicked = { uri: "file:///minutes.docx", name: "minutes.docx", mimeType: "application/msword", size: 120_000 };
    await fireEvent.press(await screen.findByTestId("document-send-group_resolution"));
    expect(await screen.findByText(/That file type is not accepted/)).toBeTruthy();
  });

  it("lets someone asked to sign join, as themselves", async () => {
    mockParams = { organisationId: "org-nakato-traders" };
    const client = seeded();
    await show(<OrganisationScreen />, client);
    expect(await screen.findByText(/Brian K. asked you to sign for this company as Director/)).toBeTruthy();
    expect(screen.getByText("You")).toBeTruthy();
    await fireEvent.press(screen.getByTestId("organisation-join"));
    expect(await screen.findByText("You sign for Nakato Traders Ltd.")).toBeTruthy();
    expect(screen.queryByTestId("organisation-join")).toBeNull();
  });

  it("goes back to the list after declining", async () => {
    mockParams = { organisationId: "org-nakato-traders" };
    const client = seeded();
    await show(<OrganisationScreen />, client);
    await fireEvent.press(await screen.findByTestId("organisation-decline"));
    await waitFor(() => expect(mockRouter.replace).toHaveBeenCalledWith("/account/organisations"));
    expect((await client.list()).map((o) => o.id)).toEqual(["org-kira-road"]);
  });

  it("shows the account number once it is open", async () => {
    mockParams = { organisationId: "org-open" };
    const base = seeded();
    const open = { ...(await base.get("org-kira-road")), id: "org-open", status: "approved" as const, accountNumber: "4000000123", problems: [] };
    await show(<OrganisationScreen />, { ...base, get: async () => open });
    expect(await screen.findByText("4000 000 123")).toBeTruthy();
    expect(screen.getByText(/Investing as a company or group is not open yet/)).toBeTruthy();
    expect(screen.queryByTestId("organisation-submit")).toBeNull();
  });
});
