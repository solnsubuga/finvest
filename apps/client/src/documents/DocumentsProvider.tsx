import { createContext, ReactNode, useContext, useMemo } from "react";

import { useAuth } from "@/auth/AuthProvider";

import { DocumentsClient, DocumentsError } from "./types";

type StubModule = typeof import("./stubDocumentsClient");

/** The demo follows the payments demo: development, or EXPO_PUBLIC_DEMO_AUTH=1 on purpose. */
function loadStub(): StubModule | null {
  if (__DEV__ || process.env.EXPO_PUBLIC_DEMO_AUTH === "1") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubDocumentsClient") as StubModule;
  }
  return null;
}

/**
 * Until the client for the real routes is written (once the typed API layer is
 * on main), a production build says the documents are unavailable rather than
 * showing anything made up.
 */
export function resolveDocumentsClient(
  phone: string | null,
  stub: StubModule | null = loadStub(),
): DocumentsClient {
  if (stub) {
    return stub.createStubDocumentsClient(phone);
  }
  const unavailable = async (): Promise<never> => {
    throw new DocumentsError("unavailable");
  };
  return {
    listDocuments: unavailable,
    getDocument: unavailable,
    requestStatement: unavailable,
    download: unavailable,
  };
}

const DocumentsContext = createContext<DocumentsClient | null>(null);

export function DocumentsProvider({
  children,
  client,
}: {
  children: ReactNode;
  client?: DocumentsClient;
}) {
  const { session } = useAuth();
  const phone = session?.phone ?? null;
  // The demo keeps each signed-in number's documents apart, so it is made for the current number.
  const resolved = useMemo(() => client ?? resolveDocumentsClient(phone), [client, phone]);
  return <DocumentsContext.Provider value={resolved}>{children}</DocumentsContext.Provider>;
}

export function useDocuments(): DocumentsClient {
  const value = useContext(DocumentsContext);
  if (!value) {
    throw new Error("useDocuments must be used inside DocumentsProvider");
  }
  return value;
}
