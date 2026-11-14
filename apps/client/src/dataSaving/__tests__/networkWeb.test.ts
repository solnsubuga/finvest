import { canTellConnection, currentConnection } from "@/dataSaving/network.web";

function connection(type: string | undefined, onLine = true) {
  Object.defineProperty(globalThis, "navigator", {
    value: { onLine, connection: type === undefined ? undefined : { type } },
    configurable: true,
  });
}

describe("the browser's network", () => {
  it("names Wi-Fi and mobile data where the browser says so", async () => {
    connection("wifi");
    expect(await currentConnection()).toBe("wifi");
    connection("ethernet");
    expect(await currentConnection()).toBe("wifi");
    connection("cellular");
    expect(await currentConnection()).toBe("mobile");
    expect(canTellConnection()).toBe(true);
  });

  it("says unknown where the browser does not tell, so nothing is held", async () => {
    connection(undefined);
    expect(await currentConnection()).toBe("unknown");
    expect(canTellConnection()).toBe(false);
    connection("other");
    expect(await currentConnection()).toBe("unknown");
  });

  it("says offline when the browser is offline", async () => {
    connection("wifi", false);
    expect(await currentConnection()).toBe("offline");
  });
});
