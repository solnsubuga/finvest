import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { askToConfirm, type ConfirmationApi } from "@/api/confirmation.ts";
import { ApiError } from "@/api/http.ts";
import { AuthContext, type AuthState } from "@/auth/context.ts";
import type { StaffSession } from "@/auth/types.ts";
import { ConfirmItIsYou, confirmationErrorMessage } from "./ConfirmItIsYou.tsx";

function session(secondSteps: StaffSession["secondSteps"]): StaffSession {
  return { staffId: "s1", name: "Grace Auma", role: "admin", permissions: [], secondSteps };
}

function setup(secondSteps: StaffSession["secondSteps"], api: Partial<ConfirmationApi> = {}) {
  const confirmation: ConfirmationApi = {
    withPasskey: vi.fn().mockResolvedValue("2026-11-13T10:10:00Z"),
    withCode: vi.fn().mockResolvedValue("2026-11-13T10:10:00Z"),
    ...api,
  };
  const auth = { session: session(secondSteps), loading: false } as AuthState;
  const view = render(
    <AuthContext value={auth}>
      <ConfirmItIsYou api={confirmation}>
        <p>screen</p>
      </ConfirmItIsYou>
    </AuthContext>,
  );
  return { confirmation, view };
}

/** Opens the dialog. The answer is wrapped, since an async function would wait for it. */
async function ask(): Promise<{ answer: Promise<boolean> }> {
  let answer!: Promise<boolean>;
  await act(async () => {
    answer = askToConfirm();
  });
  return { answer };
}

describe("confirm it is you", () => {
  it("offers the passkey first and answers yes once it is used", async () => {
    const { confirmation } = setup(["passkey", "authenticator"]);
    const { answer } = await ask();
    expect(screen.getByRole("heading", { name: "Confirm it is you" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Use passkey/ }));
    expect(await answer).toBe(true);
    expect(confirmation.withPasskey).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("heading", { name: "Confirm it is you" })).not.toBeInTheDocument();
  });

  it("asks for a code when that is all the person holds, and says when it is wrong", async () => {
    const withCode = vi
      .fn()
      .mockRejectedValueOnce(new ApiError("invalid_code", 401))
      .mockResolvedValueOnce("2026-11-13T10:10:00Z");
    setup(["authenticator"], { withCode });
    const { answer } = await ask();
    expect(screen.queryByRole("button", { name: /passkey/i })).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("6-digit code"), "111111");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("wrong or has already been used");
    await userEvent.type(screen.getByLabelText("6-digit code"), "222222");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await answer).toBe(true);
    expect(withCode).toHaveBeenLastCalledWith("222222");
  });

  it("switches to the app code, and refuses a short code without sending it", async () => {
    const { confirmation } = setup(["passkey", "authenticator"]);
    await ask();
    await userEvent.click(screen.getByRole("button", { name: "Use a code from your authenticator app" }));
    await userEvent.type(screen.getByLabelText("6-digit code"), "12");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter the 6-digit code");
    expect(confirmation.withCode).not.toHaveBeenCalled();
  });

  it("answers no when the person cancels", async () => {
    setup(["passkey"]);
    const { answer } = await ask();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await answer).toBe(false);
  });

  it("says how long to wait after too many wrong codes", async () => {
    setup(["authenticator"], { withCode: vi.fn().mockRejectedValue(new ApiError("rate_limited", 429, [], 120)) });
    await ask();
    await userEvent.type(screen.getByLabelText("6-digit code"), "111111");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Try again in 2 minutes.");
  });

  it("says a lost connection only when Finvest could not be reached", () => {
    expect(confirmationErrorMessage(new ApiError("unavailable", 0))).toMatch(/could not reach Finvest/);
    for (const code of ["unauthorized", "forbidden", "validation_failed"]) {
      expect(confirmationErrorMessage(new ApiError(code, 403))).toBe(
        "Finvest did not accept that. Sign in again if this keeps happening.",
      );
    }
  });

  it("stops asking once unmounted, leaving the request refused", async () => {
    const { view } = setup(["passkey"]);
    const { answer } = await ask();
    view.unmount();
    expect(await answer).toBe(false);
    expect(await askToConfirm()).toBe(false);
  });
});
