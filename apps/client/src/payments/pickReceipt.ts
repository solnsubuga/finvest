import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";

import { PickedFile } from "./receipt";

/** Opens the device's file chooser. Null when the client backs out. */
export async function chooseReceiptFile(): Promise<PickedFile | null> {
  const result = await DocumentPicker.getDocumentAsync({
    type: ["image/jpeg", "image/png", "application/pdf"],
    multiple: false,
    copyToCacheDirectory: true,
  });
  const asset = result.canceled ? null : result.assets[0];
  if (!asset) {
    return null;
  }
  return { uri: asset.uri, name: asset.name, mimeType: asset.mimeType, size: asset.size };
}

export type PhotoResult = PickedFile | "no_permission" | null;

/** Opens the camera for a photo of a bank slip. Null when the client backs out. */
export async function photographReceipt(): Promise<PhotoResult> {
  const permission = await ImagePicker.requestCameraPermissionsAsync();
  if (!permission.granted) {
    return "no_permission";
  }
  const result = await ImagePicker.launchCameraAsync({ mediaTypes: ["images"], quality: 0.8 });
  const asset = result.canceled ? null : result.assets[0];
  if (!asset) {
    return null;
  }
  return {
    uri: asset.uri,
    name: asset.fileName ?? "receipt-photo.jpg",
    mimeType: asset.mimeType ?? "image/jpeg",
    size: asset.fileSize,
  };
}
