import { fireEvent, render, screen } from "@testing-library/react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import CloseAccount from "@/app/account/close";
import { AuthProvider } from "@/auth/AuthProvider";
import { ProfileProvider } from "@/profile/ProfileProvider";
import { Closure, ProfileClient, Standing } from "@/profile/types";

jest.mock("expo-router", () => {
  const { useEffect } = jest.requireActual("react");
  return {
    ...jest.requireActual("expo-router"),
    useRouter: () => ({ push: jest.fn(), navigate: jest.fn(), back: jest.fn(), canGoBack: () => false }),
    useFocusEffect: (effect: () => void | (() => void)) => useEffect(effect, [effect]),
    Stack: { Screen: () => null },
  };
});

const CLEAR: Standing = {
  runningInvestments: 0,
  owed: [],
  openDeposits: 0,
  openPayouts: 0,
  openGoals: 0,
  livePlans: 0,
  blockers: [],
};

function fake(standing: Standing): ProfileClient & { asked: (string | null)[] } {
  const asked: (string | null)[] = [];
  let closure: Closure = { standing, request: null };
  const fail = () => Promise.reject(new Error("not used"));
  return {
    asked,
    getNextOfKin: fail,
    saveNextOfKin: fail,
    getRiskProfile: fail,
    answerRiskQuestions: fail,
    downloadMyData: fail,
    getClosure: async () => closure,
    askToClose: async (reason) => {
      asked.push(reason);
      closure = {
        standing,
        request: { id: "R1", status: "requested", reason, requestedAt: "2026-11-14T08:00:00Z", declineNote: null },
      };
      return closure;
    },
    cancelClosure: async () => {
      closure = { standing, request: closure.request && { ...closure.request, status: "cancelled" } };
      return closure;
    },
  };
}

async function show(client: ProfileClient) {
  await render(
    <SafeAreaProvider
      initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}
    >
      <AuthProvider>
        <ProfileProvider client={client}>
          <CloseAccount />
        </ProfileProvider>
      </AuthProvider>
    </SafeAreaProvider>,
  );
}

describe("the Close account screen", () => {
  it("lists what is still open and does not let the client ask yet", async () => {
    await show(fake({ ...CLEAR, runningInvestments: 2, owed: [{ minor: 8_164_760, currency: "UGX" }], blockers: ["investments_running", "money_owed"] }));
    expect(await screen.findByText("2 investments still running")).toBeTruthy();
    expect(screen.getByText(/UGX 8,164,760/)).toBeTruthy();
    expect(screen.getByTestId("close-withdraw-first")).toBeTruthy();
    expect(screen.getByTestId("close-start").props.accessibilityState?.disabled).toBe(true);
  });

  it("asks once everything is clear, with the client's reason, and can be taken back", async () => {
    const client = fake(CLEAR);
    await show(client);
    await fireEvent.press(await screen.findByTestId("close-start"));
    await fireEvent.changeText(screen.getByTestId("close-reason"), "  Moving abroad  ");
    await fireEvent.press(screen.getByTestId("close-confirm-button"));
    expect(await screen.findByTestId("close-requested")).toBeTruthy();
    expect(client.asked).toEqual(["Moving abroad"]);
    await fireEvent.press(screen.getByTestId("close-cancel"));
    expect(await screen.findByText("You cancelled your request. Your account stays open.")).toBeTruthy();
  });
});
