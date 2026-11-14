import {
  AnnouncementsError,
  checkAnnouncement,
  type Announcement,
  type AnnouncementsApi,
} from "./announcements.ts";

/**
 * Sample announcements for `pnpm dev` and previews. Not connected to Finvest.
 * Changes stay in this browser tab. One is waiting, proposed by someone else,
 * so the second-person step can be tried. The stub refuses what the backend's
 * PostgreSQL rules refuse.
 */

/** Active clients in the sample, as the backend would count them once sent. */
const SAMPLE_CLIENTS = 1_284;

type Stored = Omit<Announcement, "yours">;

export function createStubAnnouncementsApi(
  me: { id: string },
  now: () => number = Date.now,
  seed: "sample" | "empty" = "sample",
): AnnouncementsApi {
  let counter = 0;
  const rows: Stored[] =
    seed === "empty"
      ? []
      : [
          {
            id: "sample-announcement-2",
            title: "Office closed on Monday",
            message: "Our Kampala office is closed on Monday 17 November for a public holiday. The app works as usual.",
            reason: "Public holiday",
            status: "proposed",
            proposedBy: "sample-staff-daniel",
            proposedAt: "2026-11-13T07:10:00Z",
            decidedBy: null,
            decidedAt: null,
            decisionReason: null,
            told: 0,
          },
          {
            id: "sample-announcement-1",
            title: "New 364 day Treasury bill",
            message: "You can now invest in the 364 day Treasury bill from the app. See the details at finvestug.com/bills.",
            reason: "New product at launch",
            status: "approved",
            proposedBy: "sample-staff-grace",
            proposedAt: "2026-11-03T08:00:00Z",
            decidedBy: "sample-staff-daniel",
            decidedAt: "2026-11-03T09:30:00Z",
            decisionReason: null,
            told: SAMPLE_CLIENTS,
          },
        ];
  const view = (row: Stored): Announcement => ({ ...row, yours: row.proposedBy === me.id });
  const settle = <T>(work: () => T): Promise<T> => {
    try {
      return Promise.resolve(work());
    } catch (error) {
      return Promise.reject(error);
    }
  };

  return {
    list: () => settle(() => rows.map(view)),
    // Most clients keep news to push, as the defaults say.
    reach: () => settle(() => ({ clients: SAMPLE_CLIENTS, sms: 96, email: 310 })),
    propose: (input) =>
      settle(() => {
        const problems = checkAnnouncement(input);
        if (problems.length > 0) throw new AnnouncementsError("invalid", problems);
        const row: Stored = {
          id: `sample-announcement-new-${++counter}`,
          title: input.title,
          message: input.message,
          reason: input.reason.trim(),
          status: "proposed",
          proposedBy: me.id,
          proposedAt: new Date(now()).toISOString(),
          decidedBy: null,
          decidedAt: null,
          decisionReason: null,
          told: 0,
        };
        rows.unshift(row);
        return view(row);
      }),
    decide: (announcementId, decision, reason) =>
      settle(() => {
        const row = rows.find((item) => item.id === announcementId && item.status === "proposed");
        if (!row) throw new AnnouncementsError("not_found");
        // The proposer only withdraws, and never approves or rejects their own.
        if ((row.proposedBy === me.id) !== (decision === "withdrawn")) throw new AnnouncementsError("forbidden");
        const text = (reason ?? "").trim();
        if (decision === "rejected" && text.length === 0) {
          throw new AnnouncementsError("invalid", [{ field: "reason", code: "required" }]);
        }
        Object.assign(row, {
          status: decision,
          decidedBy: me.id,
          decidedAt: new Date(now()).toISOString(),
          decisionReason: text.length > 0 ? text : null,
          // The sample sends at once. Finvest sends a page of clients at a time.
          told: decision === "approved" ? SAMPLE_CLIENTS : 0,
        });
        return view(row);
      }),
  };
}
