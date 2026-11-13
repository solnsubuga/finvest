import { useEffect, useRef, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router";
import { useAuth } from "@/auth/context.ts";
import { LogoMark } from "./Logo.tsx";
import { Sidebar } from "./Sidebar.tsx";

/** The signed-in frame: the menu on the left and the current screen beside it. */
export function AppShell() {
  const { session, loading } = useAuth();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (menuOpen && !dialog.open) dialog.showModal();
    if (!menuOpen && dialog.open) dialog.close();
  }, [menuOpen]);

  if (loading) {
    return (
      <div role="status" className="flex min-h-dvh items-center justify-center text-md text-muted">
        Checking your sign-in
      </div>
    );
  }

  if (!session) {
    return <Navigate to="/sign-in" replace state={{ from: location.pathname }} />;
  }

  return (
    <div className="flex min-h-dvh flex-col bg-paper text-ink lg:flex-row">
      {/* Wide screens: the menu is always there. */}
      <Sidebar session={session} className="sticky top-0 hidden h-dvh lg:flex" />

      {/* Phones and narrow windows: a top bar that opens the same menu. */}
      <header className="on-black sticky top-0 z-10 flex h-14 items-center justify-between bg-black px-4 text-white lg:hidden">
        <div className="flex items-center gap-2.5">
          <LogoMark tone="dark" size={28} />
          <span className="text-lg font-extrabold">finvest ops</span>
        </div>
        <button
          type="button"
          aria-label="Open menu"
          aria-haspopup="dialog"
          onClick={() => setMenuOpen(true)}
          className="flex size-11 cursor-pointer items-center justify-center rounded-md border border-on-black-border"
        >
          <svg
            aria-hidden="true"
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
          >
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
        </button>
      </header>

      <dialog
        ref={dialogRef}
        aria-label="Menu"
        onClose={() => setMenuOpen(false)}
        onClick={(event) => {
          // A click on the backdrop lands on the dialog element itself.
          if (event.target === event.currentTarget) setMenuOpen(false);
        }}
        className="m-0 h-dvh max-h-none w-[260px] max-w-[85vw] bg-black p-0 backdrop:bg-black/50 lg:hidden"
      >
        {menuOpen ? (
          <div className="flex h-full flex-col">
            <div className="on-black flex justify-end px-4 pt-3">
              <button
                type="button"
                aria-label="Close menu"
                onClick={() => setMenuOpen(false)}
                className="flex size-11 cursor-pointer items-center justify-center rounded-md border border-on-black-border text-white"
              >
                <svg
                  aria-hidden="true"
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                >
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>
            <Sidebar session={session} onNavigate={() => setMenuOpen(false)} className="min-h-0 w-full flex-1 pt-2" />
          </div>
        ) : null}
      </dialog>

      <main className="flex min-w-0 flex-1 flex-col gap-5 px-4 py-6 sm:px-8 sm:py-7">
        <Outlet />
      </main>
    </div>
  );
}
