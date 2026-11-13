import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import { AuthProvider } from "@/auth/AuthProvider.tsx";
import { createAuthApi, createConfirmationApi, isStubAuth } from "@/auth/index.ts";
import { ConfirmItIsYou } from "@/components/ConfirmItIsYou.tsx";
import { StubBanner } from "@/components/StubBanner.tsx";
import { routes } from "@/routes.tsx";
import "./index.css";

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

const api = createAuthApi();
const confirmation = createConfirmationApi();
const router = createBrowserRouter(routes);

createRoot(root).render(
  <StrictMode>
    <AuthProvider api={api}>
      <ConfirmItIsYou api={confirmation}>
        <RouterProvider router={router} />
      </ConfirmItIsYou>
    </AuthProvider>
    {isStubAuth() ? <StubBanner /> : null}
  </StrictMode>,
);
