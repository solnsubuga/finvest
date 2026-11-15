import { toAuthError } from "@/auth/apiAuthClient";
import { describeAuthError } from "@/auth/types";
import { PasskeyAlreadyOnDevice, PasskeyCancelled } from "@/auth/webauthn";

describe("a passkey the device already holds", () => {
  it("says so, instead of blaming the connection", () => {
    const error = toAuthError(new PasskeyAlreadyOnDevice());
    expect(error.code).toBe("passkey_on_device");
    expect(describeAuthError(error)).toMatch(/already has a passkey for your Finvest account/);
    expect(describeAuthError(error)).not.toMatch(/connection/);
  });

  it("keeps a dismissed prompt as cancelled", () => {
    expect(toAuthError(new PasskeyCancelled()).code).toBe("cancelled");
  });
});
