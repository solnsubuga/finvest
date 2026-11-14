import * as ImagePicker from "expo-image-picker";

import { PickedPhoto } from "./photo";

export type PickResult = PickedPhoto | "no_permission" | null;

function picked(result: ImagePicker.ImagePickerResult, fallbackName: string): PickedPhoto | null {
  const asset = result.canceled ? null : result.assets[0];
  if (!asset) return null;
  return {
    uri: asset.uri,
    name: asset.fileName ?? fallbackName,
    mimeType: asset.mimeType ?? "image/jpeg",
    size: asset.fileSize,
  };
}

/**
 * Opens the camera. The selfie uses the front camera. On the web a browser
 * opens its own camera or file chooser. Null when the client backs out.
 */
export async function takePhoto(selfie: boolean): Promise<PickResult> {
  const permission = await ImagePicker.requestCameraPermissionsAsync();
  if (!permission.granted) return "no_permission";
  const result = await ImagePicker.launchCameraAsync({
    mediaTypes: ["images"],
    quality: 0.8,
    cameraType: selfie ? ImagePicker.CameraType.front : ImagePicker.CameraType.back,
  });
  return picked(result, selfie ? "selfie.jpg" : "id-photo.jpg");
}

/** Chooses a photo already on the device. Null when the client backs out. */
export async function choosePhoto(): Promise<PickResult> {
  const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], quality: 0.8 });
  return picked(result, "id-photo.jpg");
}
