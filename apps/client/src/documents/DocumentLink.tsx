import { useCallback, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { useLoaded } from "@/investing/useLoaded";
import { colors } from "@/theme";

import { useDocuments } from "./DocumentsProvider";
import { DocumentItem } from "./types";

/**
 * The client's document that matches, from the Documents list. Null while it
 * loads, when there is none, or when the list cannot be read: a screen shows
 * the link only when there is a document to open, and never an error for it.
 */
export function useDocumentFor(kind: "investment_note" | "receipt", id: string | undefined): DocumentItem | null {
  const client = useDocuments();
  const [found] = useLoaded(
    useCallback(async () => {
      if (!id) return null;
      const about = (document: DocumentItem) => (kind === "investment_note" ? document.investmentId : document.depositId);
      return (await client.listDocuments()).find((document) => document.kind === kind && about(document) === id) ?? null;
    }, [client, kind, id]),
  );
  return found.state === "ready" ? found.value : null;
}

/**
 * One document as a row: what it is, where it is kept, and Open, which saves
 * or opens the PDF on this device. A document still being built says so.
 */
export function DocumentLink({ document, title, testID }: { document: DocumentItem; title: string; testID?: string }) {
  const client = useDocuments();
  const [state, setState] = useState<"idle" | "opening" | "failed">("idle");
  const ready = document.status === "ready";
  const open = () => {
    setState("opening");
    client.download(document).then(
      () => setState("idle"),
      () => setState("failed"),
    );
  };
  const detail =
    state === "failed"
      ? "We could not open it. Try again, or find it in Documents."
      : ready
        ? "Signed PDF, saved in Documents"
        : document.status === "pending"
          ? "Being prepared. It appears in Documents when ready."
          : "We could not make it. We have been told and will send it again.";
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${title}, ${detail}`}
      accessibilityState={{ disabled: !ready || state === "opening", busy: state === "opening" }}
      disabled={!ready || state === "opening"}
      onPress={open}
      style={({ pressed }) => [styles.row, pressed && { opacity: 0.7 }]}
      testID={testID}
    >
      <View style={styles.icon}>
        <Icon name="download" size={18} color={colors.black} />
      </View>
      <View style={{ flex: 1, gap: 2 }}>
        <Txt weight="bold">{title}</Txt>
        <Txt size={13} color={state === "failed" ? colors.danger : colors.muted} accessibilityLiveRegion="polite">
          {detail}
        </Txt>
      </View>
      {ready ? (
        <Txt size={13} weight="bold">
          {state === "opening" ? "Opening" : "Open"}
        </Txt>
      ) : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    padding: 14,
    minHeight: 64,
  },
  icon: {
    width: 36,
    height: 36,
    borderRadius: 12,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
  },
});
