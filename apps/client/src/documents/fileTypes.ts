/** What a saved file is, for the request's Accept header and the phone's share sheet. */
export type SavedType = { mimeType: string; uti: string };

export const PDF: SavedType = { mimeType: "application/pdf", uti: "com.adobe.pdf" };

/** A statement's Excel copy. */
export const EXCEL: SavedType = {
  mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  uti: "org.openxmlformats.spreadsheetml.sheet",
};
