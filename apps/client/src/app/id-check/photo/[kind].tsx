import { useState } from "react";
import { Image, StyleSheet, View } from "react-native";
import { Redirect, Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { LoadFailed, Loading } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { useIdentity } from "@/identity/IdentityProvider";
import { StepTop } from "@/identity/Parts";
import { checkPhoto, photoProblemCopy } from "@/identity/photo";
import { choosePhoto, PickResult, takePhoto } from "@/identity/pickPhoto";
import { hrefFor, stepAfter } from "@/identity/steps";
import {
  describeIdentityError,
  IdentityCheck,
  IdentityError,
  PhotoFile,
  PhotoKind,
  photoTitle,
  requiredPhotos,
} from "@/identity/types";
import { useIdentityCheck } from "@/identity/useIdentityCheck";
import { colors, fontSizes } from "@/theme";

const KINDS: PhotoKind[] = ["front", "back", "selfie"];

const tips: Record<"document" | "selfie", string[]> = {
  document: ["All four corners showing", "No glare or shadow", "Text is sharp"],
  selfie: ["Look straight at the camera", "Your whole face in the frame", "Good light, no hat or glasses"],
};

const lookFor: Record<"document" | "selfie", string[]> = {
  document: [
    "All four corners are in the photo",
    "No glare or shadow over the text",
    "The text and numbers are readable",
    "It is the original, not a copy or a screen",
  ],
  selfie: [
    "Your whole face is in the photo",
    "Your eyes are open and you look at the camera",
    "Nothing covers your face",
  ],
};

/** Where the photo goes: a card with lime corners, or an oval for a face. */
function Frame({ selfie }: { selfie: boolean }) {
  return (
    <View style={[styles.frame, selfie && { height: 260 }]} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      <View style={[styles.corner, { top: -2, left: -2, borderTopWidth: 4, borderLeftWidth: 4, borderTopLeftRadius: 18 }]} />
      <View style={[styles.corner, { top: -2, right: -2, borderTopWidth: 4, borderRightWidth: 4, borderTopRightRadius: 18 }]} />
      <View style={[styles.corner, { bottom: -2, left: -2, borderBottomWidth: 4, borderLeftWidth: 4, borderBottomLeftRadius: 18 }]} />
      <View style={[styles.corner, { bottom: -2, right: -2, borderBottomWidth: 4, borderRightWidth: 4, borderBottomRightRadius: 18 }]} />
      {selfie ? (
        <View style={styles.face} />
      ) : (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 16, width: "100%" }}>
          <View style={styles.portrait} />
          <View style={{ flex: 1, gap: 12 }}>
            {[90, 70, 80, 50].map((width) => (
              <View key={width} style={[styles.textLine, { width: `${width}%` }]} />
            ))}
          </View>
        </View>
      )}
    </View>
  );
}

/** ID check: one photo, front, back or selfie. Also how a photo staff asked for is taken again. */
export default function TakePhoto() {
  const router = useRouter();
  const { kind: kindParam } = useLocalSearchParams<{ kind: string }>();
  const kind = KINDS.find((known) => known === kindParam) ?? null;
  const { client, markRetaken } = useIdentity();
  const state = useIdentityCheck();
  const [photo, setPhoto] = useState<PhotoFile | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!kind) return <Redirect href="/id-check" />;
  const selfie = kind === "selfie";
  const tone = selfie ? "selfie" : "document";

  if (state.status !== "ready") {
    return (
      <Screen signedIn>
        <BackButton fallback="/id-check" />
        <View style={{ marginTop: 24 }}>
          {state.status === "loading" ? (
            <Loading label="Loading your ID check" />
          ) : (
            <LoadFailed message={state.message} onRetry={state.reload} />
          )}
        </View>
      </Screen>
    );
  }

  const check = state.check;
  if (!check) return <Redirect href="/id-check/document" />;
  const retake = check.status === "awaiting_client";
  const allowed =
    requiredPhotos(check.documentType).includes(kind) &&
    (check.status === "draft" || (retake && check.retakePhotos.includes(kind)));
  if (!allowed) return <Redirect href="/id-check" />;

  const pick = async (from: () => Promise<PickResult>) => {
    setError(null);
    const result = await from();
    if (result === null) return;
    if (result === "no_permission") {
      setError(
        selfie
          ? "Finvest needs your camera for the selfie. Allow it in your phone's settings, then try again."
          : "Finvest needs your camera for this. Allow it in your phone's settings, or choose a photo you already have.",
      );
      return;
    }
    const checked = checkPhoto(result);
    if (typeof checked === "string") {
      setError(photoProblemCopy[checked]);
      return;
    }
    setPhoto(checked);
  };

  const afterUpload = async (updated: IdentityCheck) => {
    if (updated.status === "awaiting_client") {
      markRetaken(updated.id, kind);
      router.replace("/id-check");
      return;
    }
    const next = stepAfter(kind, updated);
    if (next) {
      router.push(hrefFor(next));
      return;
    }
    if (kind === "selfie") {
      // The selfie is the last step, so it hands the check in.
      try {
        await client.submit();
      } catch (caught) {
        // Whatever went wrong, the status screen reads the check again and offers to send it.
        if (!(caught instanceof IdentityError)) throw caught;
      }
    }
    router.replace("/id-check");
  };

  const send = async () => {
    if (!photo) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await client.sendPhoto(kind, photo);
      await afterUpload(updated);
    } catch (caught) {
      setError(describeIdentityError(caught));
    } finally {
      setBusy(false);
    }
  };

  const title = photoTitle(kind, check.documentType);
  const top = retake ? (
    <BackButton fallback="/id-check" />
  ) : (
    <StepTop step={kind} documentType={check.documentType} fallback="/id-check" />
  );

  if (photo) {
    return (
      <Screen
        signedIn
        footer={
          <>
            <Button label="Use this photo" onPress={send} loading={busy} testID="photo-use" />
            <TextLink label="Take it again" onPress={() => setPhoto(null)} />
          </>
        }
      >
        <Stack.Screen options={{ title }} />
        {top}
        <View style={{ marginTop: 28, gap: 8 }}>
          <Title>{selfie ? "Is your selfie clear?" : "Is the photo clear?"}</Title>
          <Txt color={colors.muted}>Have a look before you send it. A clear photo means we can confirm you sooner.</Txt>
        </View>
        <View style={{ marginTop: 20, gap: 14 }}>
          <Image
            source={{ uri: photo.uri }}
            accessibilityLabel={`The photo you took: ${title.toLowerCase()}`}
            resizeMode="cover"
            style={[styles.preview, selfie ? { aspectRatio: 3 / 4, alignSelf: "center", width: "70%" } : { aspectRatio: 1.58 }]}
            testID="photo-preview"
          />
          <Card>
            <View style={{ gap: 10 }}>
              <Txt size={fontSizes.sm} weight="bold">
                Check that
              </Txt>
              {lookFor[tone].map((line) => (
                <View key={line} style={{ flexDirection: "row", gap: 10, alignItems: "center" }}>
                  <View style={styles.bullet} />
                  <Txt size={14} style={{ flex: 1 }}>
                    {line}
                  </Txt>
                </View>
              ))}
            </View>
          </Card>
          {selfie ? (
            <Txt size={fontSizes.xs} color={colors.muted}>
              Only used to match you to your ID. It is never shown to anyone outside Finvest.
            </Txt>
          ) : null}
          {error ? <Notice tone="danger">{error}</Notice> : null}
        </View>
      </Screen>
    );
  }

  return (
    <Screen
      signedIn
      footer={
        <>
          <Button
            label={selfie ? "Take a selfie" : "Take a photo"}
            onPress={() => void pick(() => takePhoto(selfie))}
            testID="photo-take"
          />
          {selfie ? null : (
            <Button label="Choose a photo" variant="secondary" onPress={() => void pick(choosePhoto)} testID="photo-choose" />
          )}
        </>
      }
    >
      <Stack.Screen options={{ title }} />
      {top}
      <View style={{ marginTop: 28, gap: 8 }}>
        <Title>{title}</Title>
        <Txt color={colors.muted}>
          {selfie
            ? "We match it to the photo on your ID. Face the camera in good light."
            : "Lay it on a dark, flat surface and fit all of it in the photo."}
        </Txt>
      </View>
      <View style={{ marginTop: 22, gap: 16 }}>
        {retake && check.clientMessage ? <Notice tone="warning">{check.clientMessage}</Notice> : null}
        <Frame selfie={selfie} />
        <View style={styles.tips}>
          {tips[tone].map((tip) => (
            <View key={tip} style={styles.tip}>
              <Txt size={12} weight="semibold" center>
                {tip}
              </Txt>
            </View>
          ))}
        </View>
        {error ? <Notice tone="danger">{error}</Notice> : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  frame: {
    height: 200,
    borderRadius: 18,
    backgroundColor: colors.ink,
    padding: 22,
    alignItems: "center",
    justifyContent: "center",
  },
  corner: { position: "absolute", width: 34, height: 34, borderColor: colors.lime },
  portrait: { width: 80, height: 100, borderRadius: 10, borderWidth: 2, borderStyle: "dashed", borderColor: colors.neutral },
  textLine: { height: 8, borderRadius: 4, backgroundColor: colors.onBlackDivider },
  face: { width: 150, height: 196, borderRadius: 98, borderWidth: 3, borderStyle: "dashed", borderColor: colors.neutral },
  tips: { flexDirection: "row", gap: 10 },
  tip: {
    flex: 1,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 14,
    paddingVertical: 12,
    paddingHorizontal: 8,
    justifyContent: "center",
  },
  preview: { width: "100%", borderRadius: 18, backgroundColor: colors.surfaceSunken },
  bullet: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.black },
});
