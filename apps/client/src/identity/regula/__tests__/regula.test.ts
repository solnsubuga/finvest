import { outcomeOf } from "../RegulaLiveness.web";
import { finished, regulaFaceUrl, regulaTag } from "../types";

const TRANSACTION = "0F6C2B1E-5A4D-4C3B-9A2F-1E0D9C8B7A65";

describe("an attempt that runs Regula's liveness in the app", () => {
  it("is known by its page, and its tag is read from it", () => {
    expect(regulaTag("regula-liveness:KKKKKKKKKKKKKKKKKKKK")).toBe("KKKKKKKKKKKKKKKKKKKK");
    expect(regulaTag("regula-liveness:demo-liveness-3")).toBe("demo-liveness-3");
    expect(regulaTag("https://faces.iotec.io/auth?token=t")).toBeNull();
    expect(regulaTag(null)).toBeNull();
    expect(regulaTag("regula-liveness:")).toBeNull();
    expect(regulaTag("regula-liveness:a b")).toBeNull();
    expect(regulaTag(`regula-liveness:${"a".repeat(128)}`)).toBeNull();
  });

  it("uses Regula's Face service only over https", () => {
    const kept = process.env.EXPO_PUBLIC_REGULA_FACE_URL;
    try {
      process.env.EXPO_PUBLIC_REGULA_FACE_URL = "https://face.example.com/";
      expect(regulaFaceUrl()).toBe("https://face.example.com");
      process.env.EXPO_PUBLIC_REGULA_FACE_URL = "http://face.example.com";
      expect(regulaFaceUrl()).toBeNull();
      process.env.EXPO_PUBLIC_REGULA_FACE_URL = "not a url";
      expect(regulaFaceUrl()).toBeNull();
      process.env.EXPO_PUBLIC_REGULA_FACE_URL = "";
      expect(regulaFaceUrl()).toBeNull();
    } finally {
      process.env.EXPO_PUBLIC_REGULA_FACE_URL = kept;
    }
  });

  it("reports only a transaction that looks like one, in the API's lower case", () => {
    expect(finished(TRANSACTION)).toEqual({ kind: "done", transactionId: TRANSACTION.toLowerCase() });
    expect(finished("1234")).toEqual({ kind: "trouble", reason: "failed" });
    expect(finished(null)).toEqual({ kind: "trouble", reason: "failed" });
  });
});

describe("the web component's events", () => {
  it("end the session with its transaction, whatever the component judged", () => {
    expect(
      outcomeOf({ action: "PROCESS_FINISHED", data: { status: 1, response: { transactionId: TRANSACTION } } }),
    ).toEqual({ kind: "done", transactionId: TRANSACTION.toLowerCase() });
  });

  it("say when the client closed it or it could not run", () => {
    expect(outcomeOf({ action: "CLOSE" })).toEqual({ kind: "cancelled" });
    expect(outcomeOf({ action: "PROCESS_FINISHED", data: { status: 0, reason: "CANCELLED" } })).toEqual({ kind: "cancelled" });
    expect(outcomeOf({ action: "PROCESS_FINISHED", data: { status: 0, reason: "CAMERA_PERMISSION_DENIED" } })).toEqual({
      kind: "trouble",
      reason: "no_permission",
    });
    expect(outcomeOf({ action: "PROCESS_FINISHED", data: { status: 0, reason: "NO_CAMERA" } })).toEqual({
      kind: "trouble",
      reason: "no_camera",
    });
    expect(outcomeOf({ action: "PROCESS_FINISHED", data: { status: 0, reason: "CONNECTION_ERROR" } })).toEqual({
      kind: "trouble",
      reason: "connection",
    });
    expect(outcomeOf({ action: "PROCESS_FINISHED", data: { status: 0, reason: "BAD_FACE_QUALITY" } })).toEqual({
      kind: "trouble",
      reason: "failed",
    });
    expect(outcomeOf({ action: "RETRY_COUNTER_EXCEEDED" })).toEqual({ kind: "trouble", reason: "failed" });
  });

  it("keep the session running for anything else", () => {
    expect(outcomeOf({ action: "PRESS_START_BUTTON" })).toBeNull();
    expect(outcomeOf({ action: "PROCESSING_REQUEST" })).toBeNull();
    expect(outcomeOf({ action: "PROCESS_FINISHED", data: null })).toBeNull();
  });
});
