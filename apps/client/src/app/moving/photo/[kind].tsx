import { useState } from "react";
import { Image, StyleSheet, View } from "react-native";
import { Redirect, Stack, useLocalSearchParams, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { LoadFailed, Loading } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { useClaimClient } from "@/claim/ClaimProvider";
import { CLAIM_PHOTOS, ClaimPhotoKind, describeClaimError } from "@/claim/types";
import { useClaim } from "@/claim/useClaim";
import { checkPhoto, photoProblemCopy } from "@/identity/photo";
import { Frame } from "@/identity/PhotoFrame";
import { choosePhoto, PickResult, takePhoto } from "@/identity/pickPhoto";
import { PhotoFile } from "@/identity/types";
import { colors, fontSizes } from "@/theme";

const BACK = "/moving";

const copy: Record<ClaimPhotoKind, { title: string; detail: string }> = {
  front: {
    title: "Front of your ID",
    detail: "The same ID you gave Finvest 1.0. Lay it on a dark, flat surface and fit all of it in the photo.",
  },
  selfie: {
    title: "Take a selfie",
    detail: "We compare it with the photo on the ID Finvest 1.0 checked. Face the camera in good light.",
  },
};

/** One photo of the claim check: the front of the ID Finvest 1.0 checked, or a selfie. */
export default function ClaimPhoto() {
  const router = useRouter();
  const { kind: kindParam } = useLocalSearchParams<{ kind: string }>();
  const kind = CLAIM_PHOTOS.find((known) => known === kindParam) ?? null;
  const client = useClaimClient();
  const state = useClaim();
  const [photo, setPhoto] = useState<PhotoFile | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!kind) return <Redirect href={BACK} />;
  const selfie = kind === "selfie";
  const { title, detail } = copy[kind];

  if (state.status !== "ready") {
    return (
      <Screen signedIn>
        <BackButton fallback={BACK} />
        <View style={{ marginTop: 24 }}>
          {state.status === "loading" ? (
            <Loading label="Loading your photos" />
          ) : (
            <LoadFailed message={state.message} onRetry={state.reload} />
          )}
        </View>
      </Screen>
    );
  }

  const check = state.check;
  const retake = check?.status === "awaiting_client";
  const allowed = check !== null && (check.status === "draft" || (retake && check.retakePhotos.includes(kind)));
  if (!allowed) return <Redirect href={BACK} />;

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

  const send = async () => {
    if (!photo) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await client.sendPhoto(kind, photo);
      // The ID first, then the selfie. Once both are in, the moving screen hands them in.
      const next = updated.status === "draft" ? updated.missing.find((item) => item !== kind) : undefined;
      if (next) router.replace(`/moving/photo/${next}`);
      // Back to the moving screen that opened the photos, which reads the check again.
      else if (router.canGoBack()) router.back();
      else router.replace(BACK);
    } catch (caught) {
      setError(describeClaimError(caught));
      setBusy(false);
    }
  };

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
        <BackButton fallback={BACK} />
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
      <BackButton fallback={BACK} />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Title>{title}</Title>
        <Txt color={colors.muted}>{detail}</Txt>
      </View>
      <View style={{ marginTop: 22, gap: 16 }}>
        {retake && check.clientMessage ? <Notice tone="warning">{check.clientMessage}</Notice> : null}
        <Frame selfie={selfie} />
        {selfie ? (
          <Txt size={fontSizes.xs} color={colors.muted}>
            Only used to confirm it is you. It is never shown to anyone outside Finvest.
          </Txt>
        ) : null}
        {error ? <Notice tone="danger">{error}</Notice> : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  preview: { width: "100%", borderRadius: 18, backgroundColor: colors.surfaceSunken },
});
