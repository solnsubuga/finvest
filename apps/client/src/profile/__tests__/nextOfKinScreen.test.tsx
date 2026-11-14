import { fireEvent, render, screen } from "@testing-library/react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import NextOfKinScreen from "@/app/account/next-of-kin";
import { AuthProvider } from "@/auth/AuthProvider";
import { ProfileProvider } from "@/profile/ProfileProvider";
import { NextOfKin, Person, ProfileClient } from "@/profile/types";

jest.mock("expo-router", () => {
  const { useEffect } = jest.requireActual("react");
  return {
    ...jest.requireActual("expo-router"),
    useRouter: () => ({ push: jest.fn(), navigate: jest.fn(), back: jest.fn(), canGoBack: () => false }),
    useFocusEffect: (effect: () => void | (() => void)) => useEffect(effect, [effect]),
    Stack: { Screen: () => null },
  };
});

const JOSEPH: Person = { fullName: "Joseph Nakato", relationship: "spouse", phone: "+256701555210", idNumber: "CM90012345ABCD" };

function fake(people: Person[]): ProfileClient & { saved: Person[][] } {
  const saved: Person[][] = [];
  let kin: NextOfKin = { people, updatedAt: null };
  const fail = () => Promise.reject(new Error("not used"));
  return {
    saved,
    getNextOfKin: async () => kin,
    saveNextOfKin: async (next) => {
      saved.push(next);
      kin = { people: next, updatedAt: "2026-11-14T08:00:00Z" };
      return kin;
    },
    getRiskProfile: fail,
    answerRiskQuestions: fail,
    downloadMyData: fail,
    getClosure: fail,
    askToClose: fail,
    cancelClosure: fail,
  };
}

async function show(client: ProfileClient) {
  await render(
    <SafeAreaProvider
      initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}
    >
      <AuthProvider>
        <ProfileProvider client={client}>
          <NextOfKinScreen />
        </ProfileProvider>
      </AuthProvider>
    </SafeAreaProvider>,
  );
}

describe("the Next of kin screen", () => {
  it("lists people as contacts, with no share", async () => {
    await show(fake([JOSEPH]));
    expect(await screen.findByText("Joseph Nakato")).toBeTruthy();
    expect(screen.queryByText(/%/)).toBeNull();
    expect(screen.getByText(/does not give them anything you hold/)).toBeTruthy();
  });

  it("adds a person without asking for a share", async () => {
    const client = fake([JOSEPH]);
    await show(client);
    await fireEvent.press(await screen.findByTestId("kin-add"));
    expect(screen.queryByTestId("kin-share")).toBeNull();
    await fireEvent.changeText(screen.getByTestId("kin-name"), "Grace Nakato");
    await fireEvent.press(screen.getByTestId("kin-relationship-sibling"));
    await fireEvent.changeText(screen.getByTestId("phone-input"), "772310448");
    await fireEvent.changeText(screen.getByTestId("kin-id"), "CF91000000WXYZ");
    await fireEvent.press(screen.getByTestId("kin-save"));
    expect(await screen.findByText("Grace Nakato is on your list.")).toBeTruthy();
    expect(client.saved).toEqual([
      [JOSEPH, { fullName: "Grace Nakato", relationship: "sibling", phone: "+256772310448", idNumber: "CF91000000WXYZ" }],
    ]);
  });
});
