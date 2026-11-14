import type { Http } from "@/api/http.ts";
import {
  reportFilename,
  toReportsError,
  type Catalogue,
  type Currency,
  type Frequency,
  type PeriodKind,
  type ReportKey,
  type ReportsApi,
  type Schedule,
  type ScheduleStatus,
} from "./catalogue.ts";

interface ReportJson {
  key: ReportKey;
  name: string;
  about: string;
  period: PeriodKind;
  by_currency: boolean;
  date_from: string;
  date_to: string;
}

interface ScheduleJson {
  id: string;
  report_key: ReportKey;
  report_name: string;
  frequency: Frequency;
  currency: Currency | null;
  recipients: string[];
  recipient_names: string[];
  reason: string;
  status: ScheduleStatus;
  proposed_by: string;
  proposed_by_name: string | null;
  proposed_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  stopped_by: string | null;
  stopped_at: string | null;
  last_sent_at: string | null;
  yours: boolean;
}

export function toSchedule(json: ScheduleJson): Schedule {
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
        const found = await http.request<{ today: string; reports: ReportJson[] }>("GET", "/staff/reports");
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
        const found = await http.request<{
          schedules: ScheduleJson[];
          frequencies: Partial<Record<ReportKey, Frequency[]>>;
          people: { id: string; name: string; reports: ReportKey[] }[];
        }>("GET", "/staff/report-schedules");
        return { schedules: found.schedules.map(toSchedule), frequencies: found.frequencies, people: found.people };
      }),
    propose: (proposal, idempotencyKey) =>
      guarded(async () =>
        toSchedule(
          await http.request<ScheduleJson>(
            "POST",
            "/staff/report-schedules",
            {
              report_key: proposal.reportKey,
              frequency: proposal.frequency,
              currency: proposal.currency,
              recipients: proposal.recipients,
              reason: proposal.reason,
            },
            { idempotencyKey },
          ),
        ),
      ),
    decide: (id, decision, reason, idempotencyKey) =>
      guarded(async () =>
        toSchedule(
          await http.request<ScheduleJson>(
            "POST",
            `/staff/report-schedules/${encodeURIComponent(id)}/${decision}`,
            { reason },
            decision === "approve" ? { idempotencyKey } : undefined,
          ),
        ),
      ),
  };
}
