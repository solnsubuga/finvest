import { LivenessErrorCode, type LivenessResponse } from "@regulaforensics/face-sdk";

// The SDK is a native module shipped as untranspiled ES modules; its error codes are enough here.
jest.mock("@regulaforensics/face-sdk", () => ({
  LivenessErrorCode: {
    NOT_INITIALIZED: 0,
    NO_LICENSE: 1,
    API_CALL_FAILED: 2,
    SESSION_START_FAILED: 3,
    CANCELLED: 4,
    PROCESSING_TIMEOUT: 5,
    PROCESSING_FAILED: 6,
    PROCESSING_FRAME_FAILED: 7,
    APPLICATION_INACTIVE: 8,
    BAD_FACE_QUALITY: 9,
    CAMERA_NOT_AVAILABLE: 10,
    CAMERA_NO_PERMISSION: 11,
  },
  LivenessSkipStep: { ONBOARDING_STEP: 0, SUCCESS_STEP: 1 },
}));

const { outcomeOf } = jest.requireActual<typeof import("../RegulaLiveness")>("../RegulaLiveness");

const TRANSACTION = "0f6c2b1e-5a4d-4c3b-9a2f-1e0d9c8b7a65";

function response(transactionId: string | null, code?: LivenessErrorCode): LivenessResponse {
  return { transactionId, error: code === undefined ? null : { code, message: "x", underlyingError: null } } as unknown as LivenessResponse;
}

describe("the phone SDK's answer", () => {
  it("ends the session with its transaction, whatever the SDK judged", () => {
    expect(outcomeOf(response(TRANSACTION))).toEqual({ kind: "done", transactionId: TRANSACTION });
    expect(outcomeOf(response(TRANSACTION, LivenessErrorCode.PROCESSING_FAILED))).toEqual({
      kind: "done",
      transactionId: TRANSACTION,
    });
  });

  it("says when the client closed it or it could not run", () => {
    expect(outcomeOf(response(null, LivenessErrorCode.CANCELLED))).toEqual({ kind: "cancelled" });
    expect(outcomeOf(response(null, LivenessErrorCode.CAMERA_NO_PERMISSION))).toEqual({ kind: "trouble", reason: "no_permission" });
    expect(outcomeOf(response(null, LivenessErrorCode.CAMERA_NOT_AVAILABLE))).toEqual({ kind: "trouble", reason: "no_camera" });
    expect(outcomeOf(response(null, LivenessErrorCode.API_CALL_FAILED))).toEqual({ kind: "trouble", reason: "connection" });
    expect(outcomeOf(response(null, LivenessErrorCode.PROCESSING_TIMEOUT))).toEqual({ kind: "trouble", reason: "failed" });
  });
});
