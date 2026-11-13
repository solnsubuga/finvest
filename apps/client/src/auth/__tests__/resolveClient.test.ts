import { resolveAuthClient } from "../resolveClient";
import * as stub from "../stubAuthClient";

describe("resolveAuthClient", () => {
  it("uses the demo when the stub was loaded, and says so", () => {
    const resolved = resolveAuthClient(stub);
    expect(resolved.demo).toBe(true);
    expect(resolved.demoCode).toBe(stub.DEMO_CODE);
  });

  it("loads the stub in tests", () => {
    expect(resolveAuthClient().demo).toBe(true);
  });

  it("talks to the API without the stub, so the demo account is unknown there", () => {
    const { demo, demoCode } = resolveAuthClient(null);
    expect(demo).toBe(false);
    expect(demoCode).toBeNull();
  });
});
