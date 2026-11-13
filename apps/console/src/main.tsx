import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import { AuthProvider } from "@/auth/AuthProvider.tsx";
import { createAuthApi, isStubAuth } from "@/auth/index.ts";
import { StubBanner } from "@/components/StubBanner.tsx";
import { routes } from "@/routes.tsx";
import "./index.css";

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

const api = createAuthApi();
const router = createBrowserRouter(routes);

createRoot(root).render(
  <StrictMode>
    <AuthProvider api={api}>
      <RouterProvider router={router} />
    </AuthProvider>
    {isStubAuth() ? <StubBanner /> : null}
  </StrictMode>,
);
