import type { ReactNode } from "react";
import { LogoMark } from "./Logo.tsx";

/** The frame for screens outside the console: sign-in and first-time set-up. */
export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col bg-paper text-ink lg:flex-row">
      <aside className="flex flex-col justify-between gap-6 bg-black px-6 py-6 text-white lg:w-[44%] lg:max-w-[560px] lg:p-14">
        <div className="flex items-center gap-2.5">
          <LogoMark tone="dark" />
          <span className="text-xl font-extrabold">finvest ops</span>
        </div>
        <div className="hidden lg:block">
          <p className="m-0 text-3xl leading-tight font-bold tracking-[-0.4px]">The Finvest back office.</p>
          <p className="mt-3 mb-0 text-md leading-normal text-on-black-muted">
            For Finvest staff only. Everything done here is recorded with your name and a reason.
          </p>
        </div>
        {/* Keeps the logo at the top and the statement in the middle on wide screens. */}
        <span aria-hidden="true" className="hidden lg:block" />
      </aside>

      <main className="flex flex-1 items-start justify-center px-6 py-10 lg:items-center">
        <div className="flex w-full max-w-[400px] flex-col">{children}</div>
      </main>
    </div>
  );
}
