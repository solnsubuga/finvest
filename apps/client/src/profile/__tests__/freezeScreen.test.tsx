import { fireEvent, render, screen } from "@testing-library/react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import FreezeAccount from "@/app/account/freeze";
import { AuthProvider } from "@/auth/AuthProvider";
import { ProfileProvider } from "@/profile/ProfileProvider";
import { ProfileClient, ProfileError } from "@/profile/types";

const mockReplace = jest.fn();

jest.mock("expo-router", () => ({
  ...jest.requireActual("expo-router"),
  useRouter: () => ({ push: jest.fn(), navigate: jest.fn(), back: jest.fn(), canGoBack: () => false }),
  router: { replace: (...args: unknown[]) => mockReplace(...args) },
  Stack: { Screen: () => null },
}));

function fake(answer: () => Promise<{ frozenAt: string }>): ProfileClient & { asked: (string | null)[] } {
  const asked: (string | null)[] = [];
  const fail = () => Promise.reject(new Error("not used"));
  return {
    asked,
    getNextOfKin: fail,
    saveNextOfKin: fail,
    getRiskProfile: fail,
    answerRiskQuestions: fail,
    getClosure: fail,
    askToClose: fail,
    cancelClosure: fail,
    downloadMyData: fail,
    freezeAccount: (reason) => {
      asked.push(reason);
      return answer();
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
          <FreezeAccount />
        </ProfileProvider>
      </AuthProvider>
    </SafeAreaProvider>,
  );
}

describe("the Freeze my account screen", () => {
  it("says what happens, freezes with the client's reason, and then shows only the frozen screen", async () => {
    const client = fake(async () => ({ frozenAt: "2026-11-14T09:30:00Z" }));
    await show(client);
    expect(await screen.findByText("Everyone is signed out")).toBeTruthy();
    expect(screen.getByText("Two people unfreeze it")).toBeTruthy();
    await fireEvent.changeText(screen.getByTestId("freeze-reason"), "  Lost my phone ");
    await fireEvent.press(screen.getByTestId("freeze-confirm"));
    expect(await screen.findByTestId("account-frozen")).toBeTruthy();
    expect(client.asked).toEqual(["Lost my phone"]);
    expect(screen.queryByTestId("freeze-confirm")).toBeNull();
    await fireEvent.press(screen.getByTestId("frozen-done"));
    expect(mockReplace).toHaveBeenCalledWith("/welcome");
  });

  it("sends no reason when none is given, and says so when the account is already frozen", async () => {
    const client = fake(() => Promise.reject(new ProfileError("conflict")));
    await show(client);
    await fireEvent.press(await screen.findByTestId("freeze-confirm"));
    expect(await screen.findByText("Your account is already frozen or closed.")).toBeTruthy();
    expect(client.asked).toEqual([null]);
    expect(screen.queryByTestId("account-frozen")).toBeNull();
  });
});
