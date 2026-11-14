import { ComponentRef, ReactNode, Ref, useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, TextInput, View } from "react-native";

import { formatLongDay, kampalaToday } from "@/autoinvest/schedule";
import { AppPage } from "@/components/AppPage";
import { Card, Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Eyebrow, Txt } from "@/components/Txt";
import { useDocuments } from "@/documents/DocumentsProvider";
import {
  monthLabel,
  parseDay,
  periodLabel,
  periodProblem,
  pickFor,
  problemCopy,
  QUICK_PICKS,
  quickPeriod,
  QuickPick,
  receiptReference,
  taxYearLabel,
} from "@/documents/periods";
import { STATEMENT_POLL_LIMIT, STATEMENT_POLL_MS } from "@/documents/poll";
import { DocumentItem, DocumentsError } from "@/documents/types";
import { formatDateTime } from "@/payments/format";
import { newIdempotencyKey } from "@/payments/idempotency";
import { Loading } from "@/payments/Parts";
import { colors, fontFor, fontSizes, radii } from "@/theme";
import { useIsWide } from "@/theme/useIsWide";

/** Error text on the black card. The light-background red is too dark to read there. */
const ON_BLACK_DANGER = "#FF9D94";
// The box's own border shows errors. The browser's outline would sit inside it.

type ListState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; items: DocumentItem[] };

/** The black card: asking for a statement, then following it until it is ready. */
type Request =
  | { step: "form" }
  | { step: "making"; document: DocumentItem }
  | { step: "slow"; document: DocumentItem }
  | { step: "ready"; document: DocumentItem }
  | { step: "failed"; document: DocumentItem };

/**
 * Documents: ask for a statement of any period up to a year, and download
 * statements, receipts and yearly tax certificates. Every figure is in the
 * PDF, from the ledger: statements after tax, and the tax certificate with
 * the tax withheld. The screen shows no amounts of its own.
 */
export default function Documents() {
  const client = useDocuments();
  const wide = useIsWide();
  const [list, setList] = useState<ListState>({ status: "loading" });
  const [request, setRequest] = useState<Request>({ step: "form" });

  const fetchList = useCallback(
    () =>
      client.listDocuments().then(
        (items) => setList({ status: "ready", items }),
        () => setList({ status: "error" }),
      ),
    [client],
  );

  useEffect(() => {
    void fetchList();
  }, [fetchList]);

  // Follow a statement being made until it is ready or failed. Reading only.
  const following = request.step === "making" ? request.document.id : null;
  useEffect(() => {
    if (!following) {
      return;
    }
    let live = true;
    let reads = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      reads += 1;
      try {
        const document = await client.getDocument(following);
        if (!live) {
          return;
        }
        if (document.status !== "pending") {
          setRequest({ step: document.status, document });
          void fetchList();
          return;
        }
      } catch {
        // A failed read is tried again: the statement is still being made.
      }
      if (!live) {
        return;
      }
      if (reads >= STATEMENT_POLL_LIMIT) {
        setRequest((current) =>
          current.step === "making" ? { step: "slow", document: current.document } : current,
        );
        return;
      }
      timer = setTimeout(() => void tick(), STATEMENT_POLL_MS);
    };
    timer = setTimeout(() => void tick(), STATEMENT_POLL_MS);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [client, following, fetchList]);

  const asked = (document: DocumentItem) => {
    setRequest(
      document.status === "pending" ? { step: "making", document } : { step: document.status, document },
    );
    void fetchList();
  };

  const panel =
    request.step === "form" ? (
      <StatementForm onAsked={asked} />
    ) : (
      <StatementProgress
        request={request}
        onAnother={() => setRequest({ step: "form" })}
        onAsked={asked}
      />
    );

  const lists =
    list.status === "loading" ? (
      <Loading label="Loading your documents" />
    ) : list.status === "error" ? (
      <View style={{ gap: 12 }}>
        <Notice tone="danger">
          We could not load your documents. Check your connection and try again.
        </Notice>
        <Button
          label="Try again"
          variant="secondary"
          onPress={() => {
            setList({ status: "loading" });
            void fetchList();
          }}
          testID="documents-retry"
        />
      </View>
    ) : (
      <DocumentLists items={list.items} />
    );

  if (wide) {
    return (
      <AppPage title="Documents" subtitle="Statements, receipts and tax certificates for your investments.">
        <View style={styles.columns}>
          <View style={{ flex: 1, gap: 20 }}>{lists}</View>
          <View style={styles.side}>{panel}</View>
        </View>
      </AppPage>
    );
  }
  return (
    <AppPage title="Documents">
      {panel}
      {lists}
    </AppPage>
  );
}

function StatementForm({ onAsked }: { onAsked: (document: DocumentItem) => void }) {
  const client = useDocuments();
  const today = kampalaToday();
  const initial = quickPeriod("last_3_months", today);
  const [fromText, setFromText] = useState(formatLongDay(initial.from));
  const [toText, setToText] = useState(formatLongDay(initial.to));
  const [choosing, setChoosing] = useState(false);
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  // One key per period: a retry of the same period asks once, a new period asks again.
  const key = useRef<{ period: string; value: string } | null>(null);
  const fromInput = useRef<ComponentRef<typeof TextInput>>(null);

  const from = parseDay(fromText);
  const to = parseDay(toText);
  const selected: QuickPick | "choose" | null =
    choosing || !from || !to ? "choose" : (pickFor(from, to, today) ?? "choose");

  let fieldError: { field: "from" | "to"; message: string } | null = null;
  if (!from) {
    fieldError = { field: "from", message: "Type the start date as a day, month and year, such as 1 Aug 2026." };
  } else if (!to) {
    fieldError = { field: "to", message: "Type the end date as a day, month and year, such as 13 Nov 2026." };
  } else {
    const problem = periodProblem(from, to, today);
    fieldError = problem ? problemCopy(problem, from, to, today) : null;
  }

  const choose = (pick: QuickPick) => {
    const period = quickPeriod(pick, today);
    setChoosing(false);
    setFailure(null);
    setFromText(formatLongDay(period.from));
    setToText(formatLongDay(period.to));
  };

  const submit = async () => {
    if (!from || !to || fieldError) {
      return;
    }
    const period = `${from}/${to}`;
    if (key.current?.period !== period) {
      key.current = { period, value: newIdempotencyKey() };
    }
    setSending(true);
    setFailure(null);
    try {
      onAsked(await client.requestStatement(from, to, key.current.value));
    } catch (error) {
      setFailure(requestFailure(error, from, to, today));
    } finally {
      setSending(false);
    }
  };

  return (
    <View style={styles.black} testID="statement-form">
      <View style={{ gap: 4 }}>
        <Txt size={fontSizes["2xl"]} weight="extrabold" color={colors.white}>
          Get a statement
        </Txt>
        <Txt size={fontSizes.md} color={colors.onBlackMuted}>
          Every investment you hold, after tax.
        </Txt>
      </View>
      <View style={styles.chips} accessibilityRole="radiogroup">
        {QUICK_PICKS.map(({ pick, label }) => (
          <Chip key={pick} label={label} selected={selected === pick} onPress={() => choose(pick)} testID={`pick-${pick}`} />
        ))}
        <Chip
          label="Choose dates"
          selected={selected === "choose"}
          onPress={() => {
            setChoosing(true);
            fromInput.current?.focus();
          }}
          testID="pick-choose"
        />
      </View>
      <View style={{ gap: 8 }}>
        <View style={styles.dates}>
          <DateInput
            ref={fromInput}
            label="From"
            value={fromText}
            invalid={fieldError?.field === "from"}
            onChangeText={(text) => {
              setChoosing(true);
              setFailure(null);
              setFromText(text);
            }}
            testID="statement-from"
          />
          <DateInput
            label="To"
            value={toText}
            invalid={fieldError?.field === "to"}
            onChangeText={(text) => {
              setChoosing(true);
              setFailure(null);
              setToText(text);
            }}
            testID="statement-to"
          />
        </View>
        {fieldError ? (
          <Txt size={fontSizes.sm} weight="semibold" color={ON_BLACK_DANGER} accessibilityLiveRegion="polite" testID="statement-date-error">
            {fieldError.message}
          </Txt>
        ) : null}
      </View>
      <Button
        label="Make statement"
        variant="lime"
        disabled={fieldError !== null}
        loading={sending}
        onPress={() => void submit()}
        testID="make-statement"
      />
      {failure ? (
        <Txt size={fontSizes.sm} weight="semibold" color={ON_BLACK_DANGER} accessibilityRole="alert">
          {failure}
        </Txt>
      ) : null}
      <Txt size={fontSizes.sm} color={colors.onBlackMuted}>
        We keep it here to download, and email the PDF to you if your profile has an email address.
        Any period up to one year.
      </Txt>
    </View>
  );
}

function requestFailure(error: unknown, from: string, to: string, today: string): string {
  const code = error instanceof DocumentsError ? error.code : "offline";
  if (error instanceof DocumentsError && code === "validation_failed" && error.field) {
    return problemCopy(error.field, from, to, today).message;
  }
  if (code === "rate_limited") {
    // The API does not say how many, so the screen does not either.
    return "You have asked for as many statements as one day allows. You can ask again tomorrow.";
  }
  if (code === "offline") {
    return "We could not reach Finvest. Check your connection and try again.";
  }
  // Asking again sends the same key, so it never makes a second statement.
  return "Something went wrong on our side. Try again in a few minutes.";
}

function StatementProgress({
  request,
  onAnother,
  onAsked,
}: {
  request: Exclude<Request, { step: "form" }>;
  onAnother: () => void;
  onAsked: (document: DocumentItem) => void;
}) {
  const client = useDocuments();
  const { document } = request;
  const period = document.dateFrom && document.dateTo ? periodLabel(document.dateFrom, document.dateTo) : "";
  const [retrying, setRetrying] = useState(false);
  const [retryFailed, setRetryFailed] = useState(false);

  if (request.step === "making" || request.step === "slow") {
    return (
      <View style={styles.black} testID="statement-making">
        <View style={{ gap: 4 }}>
          <Txt size={fontSizes["2xl"]} weight="extrabold" color={colors.white}>
            Making your statement
          </Txt>
          <Txt size={fontSizes.md} color={colors.onBlackMuted}>
            {period}
          </Txt>
        </View>
        <View style={styles.spinnerRow}>
          <ActivityIndicator color={colors.lime} aria-hidden />
          <Txt size={fontSizes.lg} weight="bold" color={colors.white}>
            {request.step === "slow" ? "Taking longer than usual" : "Adding up your investments"}
          </Txt>
        </View>
        <Txt size={fontSizes.sm} color={colors.onBlackMuted}>
          {request.step === "slow"
            ? "It will be in your list below when it is ready, and we email it to you if you have an email address. You can leave this screen."
            : "This usually takes under a minute. You can leave this screen: it will be here when you come back, and we email it to you too."}
        </Txt>
      </View>
    );
  }

  if (request.step === "failed") {
    const retry = async () => {
      if (!document.dateFrom || !document.dateTo) {
        return;
      }
      setRetrying(true);
      setRetryFailed(false);
      try {
        // A new key: the failed statement stays failed, and this is a new one.
        onAsked(await client.requestStatement(document.dateFrom, document.dateTo, newIdempotencyKey()));
      } catch {
        setRetryFailed(true);
      } finally {
        setRetrying(false);
      }
    };
    return (
      <View style={styles.black} testID="statement-failed">
        <View style={{ gap: 4 }}>
          <Txt size={fontSizes["2xl"]} weight="extrabold" color={colors.white}>
            We could not make that statement
          </Txt>
          <Txt size={fontSizes.md} color={colors.onBlackMuted}>
            {period}
          </Txt>
        </View>
        <Txt size={fontSizes.md} weight="semibold" color={ON_BLACK_DANGER} accessibilityRole="alert">
          {retryFailed
            ? "We could not reach Finvest. Check your connection and try again."
            : "Something went wrong on our side. Nothing about your money has changed."}
        </Txt>
        <Button label="Try again" variant="lime" loading={retrying} onPress={() => void retry()} testID="statement-retry" />
        <Txt size={fontSizes.sm} color={colors.onBlackMuted}>
          {`If it keeps happening, ask us through Help and quote ${document.id}.`}
        </Txt>
      </View>
    );
  }

  return (
    <View style={styles.black} testID="statement-ready">
      <View style={styles.readyBadge}>
        <Txt size={fontSizes.xs} weight="bold" color={colors.limeText}>
          Ready
        </Txt>
      </View>
      <View style={{ gap: 4 }}>
        <Txt size={fontSizes["2xl"]} weight="extrabold" color={colors.white}>
          Your statement is ready
        </Txt>
        <Txt size={fontSizes.md} color={colors.onBlackMuted}>
          {period}
        </Txt>
      </View>
      <DownloadButton document={document} />
      <Button label="Make another" variant="outlineOnDark" onPress={onAnother} testID="statement-another" />
      {document.notFinalFrom ? (
        <Txt size={fontSizes.sm} color={colors.onBlackMuted}>
          {notFinalCopy(document.notFinalFrom)}
        </Txt>
      ) : null}
      <Txt size={fontSizes.sm} color={colors.onBlackMuted}>
        If your profile has an email address, we email it to you too. It can take a few minutes to
        arrive.
      </Txt>
    </View>
  );
}

function notFinalCopy(day: string): string {
  return `Figures from ${formatLongDay(day)} may still change until our books for that day are closed. The PDF says so too.`;
}

/** The lime "Download PDF" on the ready card. */
function DownloadButton({ document }: { document: DocumentItem }) {
  const { download, state } = useDownload(document);
  return (
    <View style={{ gap: 8 }}>
      <Button
        label={state === "done" ? "Downloaded" : "Download PDF"}
        variant="lime"
        loading={state === "busy"}
        onPress={download}
        testID="statement-download"
      />
      {state === "failed" ? (
        <Txt size={fontSizes.sm} weight="semibold" color={ON_BLACK_DANGER} accessibilityRole="alert">
          The download did not finish. Try again.
        </Txt>
      ) : null}
    </View>
  );
}

type DownloadState = "idle" | "busy" | "done" | "failed";

function useDownload(document: DocumentItem) {
  const client = useDocuments();
  const [state, setState] = useState<DownloadState>("idle");
  const download = () => {
    setState("busy");
    client.download(document).then(
      () => setState("done"),
      () => setState("failed"),
    );
  };
  return { download, state };
}

function DocumentLists({ items }: { items: DocumentItem[] }) {
  const requested = items.filter((item) => item.kind === "requested_statement");
  const monthly = items.filter((item) => item.kind === "monthly_statement" && item.status === "ready");
  const receipts = items.filter((item) => item.kind === "receipt" && item.status === "ready");
  const certificates = items.filter(
    (item) => item.kind === "tax_certificate" && item.status === "ready",
  );

  return (
    <View style={{ gap: 20 }}>
      {requested.length === 0 && monthly.length === 0 ? (
        <Section title="Statements">
          <Card>
            <Txt size={fontSizes.md} testID="statements-empty">
              No statements yet. Your first monthly statement arrives here and by email after the end of
              the first month you hold an investment.
            </Txt>
          </Card>
        </Section>
      ) : null}
      {requested.length > 0 ? (
        <Section title="Statements you made">
          <Rows items={requested} />
        </Section>
      ) : null}
      {monthly.length > 0 ? (
        <Section title="Monthly statements">
          <Rows items={monthly} />
        </Section>
      ) : null}
      {certificates.length > 0 ? (
        <Section title="Tax certificates">
          <Rows items={certificates} />
        </Section>
      ) : null}
      <Section title="Receipts">
        {receipts.length > 0 ? (
          <Rows items={receipts} />
        ) : (
          <Card>
            <Txt size={fontSizes.md} testID="receipts-empty">
              No receipts yet. You get one here and by email each time a payment reaches us.
            </Txt>
          </Card>
        )}
      </Section>
    </View>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <View style={{ gap: 10 }}>
      <Eyebrow>{title}</Eyebrow>
      {children}
    </View>
  );
}

function Rows({ items }: { items: DocumentItem[] }) {
  return (
    <Card padded={false}>
      {items.map((item, index) => (
        <DocumentRow key={item.id} document={item} first={index === 0} />
      ))}
    </Card>
  );
}

function rowText(document: DocumentItem): { title: string; subtitle: string } {
  if (document.kind === "receipt") {
    const reference = receiptReference(document);
    return {
      title: reference ? `Receipt ${reference}` : "Receipt",
      subtitle: `Payment confirmed on ${formatDateTime(document.requestedAt)}`,
    };
  }
  if (document.kind === "tax_certificate" && document.dateFrom && document.dateTo) {
    return {
      title: `Tax year ${taxYearLabel(document.dateFrom)}`,
      subtitle: `${periodLabel(document.dateFrom, document.dateTo)}. Shows the tax withheld from your interest.`,
    };
  }
  if (document.kind === "monthly_statement" && document.dateFrom) {
    return {
      title: monthLabel(document.dateFrom),
      subtitle: `Made on ${formatDateTime(document.finishedAt ?? document.requestedAt)}`,
    };
  }
  const title =
    document.dateFrom && document.dateTo ? periodLabel(document.dateFrom, document.dateTo) : "Statement";
  if (document.status === "pending") {
    return { title, subtitle: "Being made now" };
  }
  if (document.status === "failed") {
    return { title, subtitle: "Could not be made. Ask for it again." };
  }
  return {
    title,
    subtitle: document.notFinalFrom
      ? `Made on ${formatDateTime(document.requestedAt)}. Figures from ${formatLongDay(document.notFinalFrom)} may still change.`
      : `Made on ${formatDateTime(document.requestedAt)}`,
  };
}

function DocumentRow({ document, first }: { document: DocumentItem; first: boolean }) {
  const { title, subtitle } = rowText(document);
  const { download, state } = useDownload(document);
  return (
    <View style={[styles.row, !first && styles.rowDivider]}>
      <View style={styles.pdfTile}>
        <Txt size={fontSizes.xs} weight="extrabold">
          PDF
        </Txt>
      </View>
      <View style={{ flex: 1, gap: 2 }}>
        <Txt size={fontSizes.lg} weight="bold">
          {title}
        </Txt>
        <Txt size={fontSizes.sm} color={state === "failed" ? colors.danger : colors.muted}>
          {state === "failed" ? "The download did not finish. Try again." : subtitle}
        </Txt>
      </View>
      {document.status === "ready" ? (
        <Pressable
          onPress={download}
          disabled={state === "busy"}
          accessibilityRole="button"
          accessibilityLabel={`Download ${title}`}
          style={({ pressed }) => [styles.roundButton, pressed && { opacity: 0.7 }]}
          testID={`download-${document.id}`}
        >
          {state === "busy" ? (
            <ActivityIndicator color={colors.ink} accessibilityLabel="Downloading" />
          ) : (
            <Icon name={state === "done" ? "check" : "download"} size={20} />
          )}
        </Pressable>
      ) : document.status === "pending" ? (
        <View style={styles.roundButton}>
          <ActivityIndicator color={colors.ink} accessibilityLabel="Being made" />
        </View>
      ) : null}
    </View>
  );
}

function Chip({
  label,
  selected,
  onPress,
  testID,
}: {
  label: string;
  selected: boolean;
  onPress: () => void;
  testID: string;
}) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="radio"
      aria-checked={selected}
      style={[styles.chip, selected && styles.chipSelected]}
      testID={testID}
    >
      <Txt size={fontSizes.md} weight="bold" color={selected ? colors.ink : colors.white}>
        {label}
      </Txt>
    </Pressable>
  );
}

type DateInputProps = {
  label: string;
  value: string;
  invalid: boolean;
  onChangeText: (text: string) => void;
  testID: string;
  ref?: Ref<ComponentRef<typeof TextInput>>;
};

function DateInput({ label, value, invalid, onChangeText, testID, ref }: DateInputProps) {
  return (
    <View style={{ flex: 1, gap: 6 }}>
      <Txt size={fontSizes.sm} weight="bold" color={colors.white}>
        {label}
      </Txt>
      <View style={[styles.dateBox, invalid && styles.dateInvalid]}>
        <TextInput
          ref={ref}
          value={value}
          onChangeText={onChangeText}
          accessibilityLabel={`${label} date`}
          placeholder="1 Aug 2026"
          placeholderTextColor={colors.onBlackMuted}
          autoCorrect={false}
          autoCapitalize="none"
          style={styles.dateText}
          testID={testID}
        />
        <Icon name="calendar" size={18} color={colors.onBlackMuted} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  columns: { flexDirection: "row", gap: 24, alignItems: "flex-start" },
  side: { width: 380 },
  black: { backgroundColor: colors.black, borderRadius: radii.xl + 4, padding: 20, gap: 16 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    paddingHorizontal: 14,
    paddingVertical: 9,
    // A 44 point touch target, as every control in the app has.
    minHeight: 44,
    justifyContent: "center",
    borderRadius: radii.pill,
    backgroundColor: colors.onBlackSurface,
    borderWidth: 1,
    borderColor: colors.onBlackDivider,
  },
  chipSelected: { backgroundColor: colors.lime, borderColor: colors.lime },
  dates: { flexDirection: "row", gap: 10 },
  dateBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    height: 50,
    paddingHorizontal: 12,
    borderRadius: radii.md,
    backgroundColor: colors.onBlackSurface,
    borderWidth: 2,
    borderColor: colors.onBlackSurface,
  },
  dateInvalid: { borderColor: ON_BLACK_DANGER },
  dateText: {
    flex: 1,
    minWidth: 0,
    color: colors.white,
    fontFamily: fontFor("bold"),
    fontSize: 15,
    paddingVertical: 0,
    // The whole box takes the tap, not only the line of text.
    alignSelf: "stretch",
  },
  spinnerRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  readyBadge: {
    alignSelf: "flex-start",
    backgroundColor: colors.limeSurface,
    borderRadius: radii.pill,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  row: { flexDirection: "row", alignItems: "center", gap: 14, paddingHorizontal: 16, paddingVertical: 14 },
  rowDivider: { borderTopWidth: 1, borderTopColor: colors.divider },
  pdfTile: {
    width: 44,
    height: 44,
    borderRadius: radii.md,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
  },
  roundButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    borderWidth: 1.5,
    borderColor: colors.borderStrong,
    alignItems: "center",
    justifyContent: "center",
  },
});
