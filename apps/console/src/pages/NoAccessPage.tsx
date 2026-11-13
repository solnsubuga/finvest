import { Link } from "react-router";
import { useAuth } from "@/auth/context.ts";
import { ROLE_LABELS } from "@/auth/types.ts";
import { homePath } from "@/nav/nav.ts";
import { PageHeader } from "./PageHeader.tsx";

export function NoAccessPage() {
  const { session } = useAuth();
  const home = session ? homePath(session) : null;

  return (
    <>
      <PageHeader title="You cannot open this screen" summary="Your role does not include it." />
      <section className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
        <p className="m-0 text-md leading-normal text-muted">
          {session ? `You are signed in as ${ROLE_LABELS[session.role]}. ` : ""}
          If you need this screen for your work, ask an admin to change your role.
        </p>
        {home ? (
          <Link to={home} className="self-start text-md font-bold text-ink underline">
            Back to your first screen
          </Link>
        ) : null}
      </section>
    </>
  );
}
