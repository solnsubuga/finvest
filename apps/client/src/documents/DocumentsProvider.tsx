import { createContext, ReactNode, useContext, useMemo } from "react";

import { useAuth } from "@/auth/AuthProvider";

import { createApiDocumentsClient } from "./apiDocumentsClient";
import { DocumentsClient } from "./types";

type StubModule = typeof import("./stubDocumentsClient");

/**
 * The demo loads only in a demo build (EXPO_PUBLIC_DEMO_AUTH=1) or under tests, like every
 * other area, so a development build against a local backend talks to the real API.
 */
function loadStub(): StubModule | null {
  if (process.env.EXPO_PUBLIC_DEMO_AUTH === "1" || process.env.NODE_ENV === "test") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubDocumentsClient") as StubModule;
  }
  return null;
}

/** The demo stub in development or a demo build; the real API otherwise. */
export function resolveDocumentsClient(
  phone: string | null,
  stub: StubModule | null = loadStub(),
): DocumentsClient {
  return stub ? stub.createStubDocumentsClient(phone) : createApiDocumentsClient();
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
