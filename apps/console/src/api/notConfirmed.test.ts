import { describe, expect, it } from "vitest";
import { clientsErrorMessage, toClientsError } from "@/screens/clients/api.ts";
import { suppressionsErrorMessage, toSuppressionsError } from "@/screens/clients/suppressions.ts";
import { bufferErrorMessage } from "@/screens/dealing-desk/buffer.ts";
import { toBufferError } from "@/screens/dealing-desk/bufferHttp.ts";
import { complaintsErrorMessage } from "@/screens/complaints/api.ts";
import { toComplaintsError } from "@/screens/complaints/http.ts";
import { kycErrorMessage } from "@/screens/identity-checks/api.ts";
import { toKycError } from "@/screens/identity-checks/http.ts";
import { announcementsErrorMessage } from "@/screens/settings/announcements.ts";
import { wordingsErrorMessage } from "@/screens/settings/api.ts";
import { toAnnouncementsError, toWordingsError } from "@/screens/settings/http.ts";
import { teamErrorMessage } from "@/screens/team/api.ts";
import { toTeamError } from "@/screens/team/http.ts";
import { CONFIRM_IT_IS_YOU } from "./confirmation.ts";
import { ApiError } from "./http.ts";

/** Every screen whose decisions need the person to confirm it is them. */
const SCREENS: [string, (error: unknown) => unknown, (error: unknown) => string][] = [
  ["team", (e) => toTeamError(e, true), teamErrorMessage],
  ["wordings", (e) => toWordingsError(e, true), wordingsErrorMessage],
  ["announcements", toAnnouncementsError, announcementsErrorMessage],
  ["complaints", toComplaintsError, complaintsErrorMessage],
  ["identity checks", toKycError, kycErrorMessage],
  ["client locks", (e) => toClientsError(e, true), clientsErrorMessage],
  ["suppression lifts", (e) => toSuppressionsError(e, true), suppressionsErrorMessage],
  ["liquidity buffer", toBufferError, bufferErrorMessage],
];

describe("a decision the person did not confirm", () => {
  it.each(SCREENS)("says so on %s, rather than that Finvest could not be reached", (_, toError, message) => {
    const error = toError(new ApiError(CONFIRM_IT_IS_YOU, 403));
    expect(error).toMatchObject({ code: "not_confirmed" });
    expect(message(error)).toBe("Not done. Confirm it is you with your passkey or authenticator app, then try again.");
  });
});
