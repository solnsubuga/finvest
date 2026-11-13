import { afterEach, describe, expect, it, vi } from "vitest";
import { askToConfirm, createHttpConfirmationApi, setConfirmer } from "./confirmation.ts";
import type { Http } from "./http.ts";

let remove: (() => void) | null = null;
afterEach(() => remove?.());

describe("asking to confirm", () => {
  it("answers no when no dialog is mounted", async () => {
    expect(await askToConfirm()).toBe(false);
  });

  it("asks once for requests refused at the same moment", async () => {
    let answer: (value: boolean) => void = () => undefined;
    const confirmer = vi.fn(() => new Promise<boolean>((resolve) => (answer = resolve)));
    remove = setConfirmer(confirmer);
    const first = askToConfirm();
    const second = askToConfirm();
    answer(true);
    expect(await Promise.all([first, second])).toEqual([true, true]);
    expect(confirmer).toHaveBeenCalledTimes(1);
    // A later refusal asks again.
    const third = askToConfirm();
    answer(false);
    expect(await third).toBe(false);
    expect(confirmer).toHaveBeenCalledTimes(2);
  });

  it("stops asking once the dialog is removed", async () => {
    const confirmer = vi.fn(() => Promise.resolve(true));
    setConfirmer(confirmer)();
    expect(await askToConfirm()).toBe(false);
    expect(confirmer).not.toHaveBeenCalled();
  });
});

describe("the confirmation routes", () => {
  function http(...bodies: unknown[]) {
    const request = vi.fn();
    for (const body of bodies) request.mockResolvedValueOnce(body);
    return { request, http: { request, download: vi.fn() } as unknown as Http };
  }

  it("signs the passkey challenge the API sends", async () => {
    const { request, http: client } = http({ public_key: { challenge: "abc" } }, { confirmed_until: "2026-11-13T10:10:00Z" });
    const passkeys = { create: vi.fn(), get: vi.fn().mockResolvedValue({ id: "cred" }) };
    expect(await createHttpConfirmationApi(client, passkeys).withPasskey()).toBe("2026-11-13T10:10:00Z");
    expect(request.mock.calls).toEqual([
      ["POST", "/staff/session/confirmation/passkey/options"],
      ["POST", "/staff/session/confirmation/passkey", { credential: { id: "cred" } }],
    ]);
    expect(passkeys.get).toHaveBeenCalledWith({ challenge: "abc" });
  });

  it("sends an authenticator code", async () => {
    const { request, http: client } = http({ confirmed_until: "2026-11-13T10:10:00Z" });
    const passkeys = { create: vi.fn(), get: vi.fn() };
    await createHttpConfirmationApi(client, passkeys).withCode("123456");
    expect(request).toHaveBeenCalledWith("POST", "/staff/session/confirmation/authenticator", { code: "123456" });
  });
});
