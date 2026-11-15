import { NavLink } from "react-router";
import { useAuth } from "@/auth/context.ts";
import { ROLE_LABELS, type StaffSession } from "@/auth/types.ts";
import { cn, initials } from "@/lib.ts";
import { badge, type MenuCounts } from "@/nav/counts.ts";
import { visibleNav } from "@/nav/nav.ts";
import { LogoMark } from "./Logo.tsx";
import { Button } from "./ui/button.tsx";

interface SidebarProps {
  session: StaffSession;
  /** Work waiting on each screen, by path. A screen with none shows no number. */
  counts?: MenuCounts;
  /** Called after a link is chosen, so the phone drawer can close. */
  onNavigate?: () => void;
  className?: string;
}

export function Sidebar({
  session,
  counts = {},
  onNavigate,
  className,
}: SidebarProps) {
  const { signOut } = useAuth();
  const groups = visibleNav(session);

  return (
    <nav
      aria-label="Console"
      className={cn(
        "on-black flex w-[236px] shrink-0 flex-col gap-1.5 bg-black px-4 py-4 text-white",
        className,
      )}
    >
      <div className="flex items-center gap-2.5 px-2">
        <LogoMark tone="dark" />
        <span className="text-xl font-extrabold">finvest ops</span>
      </div>

      {/* Sized so all 21 screens fit at 1440 by 900 without scrolling, as on the canvas. */}
      <div className="menu-scroll -mr-2.5 flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto pr-2.5">
        {groups.map((group) => (
          <section
            key={group.title}
            aria-label={group.title}
            className="flex flex-col gap-0.5"
          >
            <h2 className="px-3 pb-0.5 text-2xs font-bold tracking-[0.8px] text-on-black-muted uppercase">
              {group.title}
            </h2>
            {group.items.map((item) => {
              const shown = badge(counts[item.path]);
              return (
                <NavLink
                  key={item.path}
                  to={`/${item.path}`}
                  {...(onNavigate ? { onClick: onNavigate } : {})}
                  className={({ isActive }) =>
                    cn(
                      "ml-2.5 flex h-11 shrink-0 items-center lg:h-7 justify-between gap-2 rounded-sm px-2.5 text-sm whitespace-nowrap",
                      isActive
                        ? "bg-lime font-bold text-ink"
                        : "font-semibold text-white hover:bg-on-black-surface",
                    )
                  }
                >
                  {item.label}
                  {shown ? (
                    <>
                      <span
                        aria-hidden="true"
                        data-testid={`menu-count-${item.path}`}
                        className="font-bold tabular-nums"
                      >
                        {shown}
                      </span>
                      <span className="sr-only">, {shown} waiting</span>
                    </>
                  ) : null}
                </NavLink>
              );
            })}
          </section>
        ))}
      </div>

      <div className="flex items-center gap-2.5 border-t border-on-black-divider px-2 pt-2.5">
        <span
          aria-hidden="true"
          className="flex size-9 shrink-0 items-center justify-center rounded-pill bg-on-black-surface text-sm font-extrabold"
        >
          {initials(session.name)}
        </span>
        <p className="m-0 text-xs text-on-black-muted">
          Signed in as
          <br />
          <strong className="text-sm text-white">
            {session.name}, {ROLE_LABELS[session.role]}
          </strong>
        </p>
      </div>
      <div className="flex items-center gap-2">
        <NavLink
          to="/devices"
          {...(onNavigate ? { onClick: onNavigate } : {})}
          className={({ isActive }) =>
            cn(
              "flex h-11 shrink-0 items-center justify-center rounded-md px-2 text-sm whitespace-nowrap",
              isActive
                ? "bg-lime font-bold text-ink"
                : "font-semibold text-white hover:bg-on-black-surface",
            )
          }
        >
          Your devices
        </NavLink>
        <Button
          variant="onBlack"
          size="block"
          className="flex-1 gap-1.5 px-2.5 whitespace-nowrap"
          onClick={() => void signOut()}
        >
          <svg
            aria-hidden="true"
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M10 4H5v16h5M15 8l4 4-4 4M19 12H9" />
          </svg>
          Sign Out
        </Button>
      </div>
    </nav>
  );
}
