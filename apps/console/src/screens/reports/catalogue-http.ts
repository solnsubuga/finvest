import type { CatalogueOut, ProposeScheduleIn, ScheduleOut, SchedulesOut, DecideScheduleIn } from "@finvest/api-client/staff";
import type { Http } from "@/api/http.ts";
import {
  reportFilename,
  toReportsError,
  type Catalogue,
  type ReportKey,
  type ReportsApi,
  type Schedule,
} from "./catalogue.ts";

export function toSchedule(json: ScheduleOut): Schedule {
  return {
    id: json.id,
    reportKey: json.report_key,
    reportName: json.report_name,
    frequency: json.frequency,
    currency: json.currency,
    recipients: json.recipients,
    recipientNames: json.recipient_names,
    reason: json.reason,
    status: json.status,
    proposedBy: json.proposed_by,
    proposedByName: json.proposed_by_name,
    proposedAt: json.proposed_at,
    decidedBy: json.decided_by,
    decidedAt: json.decided_at,
    decisionReason: json.decision_reason,
    stoppedBy: json.stopped_by,
    stoppedAt: json.stopped_at,
    lastSentAt: json.last_sent_at,
    yours: json.yours,
  };
}

async function guarded<T>(work: () => Promise<T>): Promise<T> {
  try {
    return await work();
  } catch (error) {
    throw toReportsError(error);
  }
}

export function createHttpReportsApi(http: Http): ReportsApi {
  let byCurrency = new Set<ReportKey>();
  return {
    catalogue: () =>
      guarded(async () => {
        const found = await http.request<CatalogueOut>("GET", "/staff/reports");
        const catalogue: Catalogue = {
          today: found.today,
          reports: found.reports.map((report) => ({
            key: report.key,
            name: report.name,
            about: report.about,
            period: report.period,
            byCurrency: report.by_currency,
            from: report.date_from,
            to: report.date_to,
          })),
        };
        byCurrency = new Set(catalogue.reports.filter((report) => report.byCurrency).map((report) => report.key));
        return catalogue;
      }),
    download: (key, period) =>
      guarded(async () => {
        const query = new URLSearchParams({ date_from: period.from, date_to: period.to, currency: period.currency });
        const file = await http.download(`/staff/reports/${key}.csv?${query.toString()}`);
        return { file, filename: reportFilename(key, byCurrency.has(key) ? period.currency : null, period.from, period.to) };
      }),
    schedules: () =>
      guarded(async () => {
        const found = await http.request<SchedulesOut>("GET", "/staff/report-schedules");
        return { schedules: found.schedules.map(toSchedule), frequencies: found.frequencies, people: found.people };
      }),
    propose: (proposal, idempotencyKey) =>
      guarded(async () =>
        toSchedule(
          await http.request<ScheduleOut>(
            "POST",
            "/staff/report-schedules",
            {
              report_key: proposal.reportKey,
              frequency: proposal.frequency,
              currency: proposal.currency,
              recipients: proposal.recipients,
              reason: proposal.reason,
            } satisfies ProposeScheduleIn,
            { idempotencyKey },
          ),
        ),
      ),
    decide: (id, decision, reason, idempotencyKey) =>
      guarded(async () =>
        toSchedule(
          await http.request<ScheduleOut>(
            "POST",
            `/staff/report-schedules/${encodeURIComponent(id)}/${decision}`,
            { reason } satisfies DecideScheduleIn,
            decision === "approve" ? { idempotencyKey } : undefined,
          ),
        ),
      ),
  };
}
