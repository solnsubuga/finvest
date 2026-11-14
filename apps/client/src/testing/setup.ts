// The phone app asks for a PIN after every log in. Tests of other screens skip it, and the
// PIN's own tests turn it back on with `jest.mocked(appPinApplies).mockReturnValue(true)`.
jest.mock("@/auth/pin", () => {
  const actual = jest.requireActual("@/auth/pin");
  return { ...actual, appPinApplies: jest.fn(() => false) };
});
