import { useId, useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router";
import { authErrorMessage, type PasskeyChallenge } from "@/auth/api.ts";
import { useAuth } from "@/auth/context.ts";
import { LogoMark } from "@/components/Logo.tsx";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";

function fromPath(state: unknown): string {
  if (typeof state === "object" && state !== null && "from" in state) {
    const from = (state as { from: unknown }).from;
    // Only follow a path inside the console, never an outside address.
    if (typeof from === "string" && from.startsWith("/") && !from.startsWith("//")) return from;
  }
  return "/";
}

export function SignInPage() {
  const { session, loading, startSignIn, finishSignIn } = useAuth();
  const location = useLocation();
  const emailId = useId();
  const passwordId = useId();
  const errorId = useId();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState<PasskeyChallenge | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!loading && session) {
    return <Navigate to={fromPath(location.state)} replace />;
  }

  async function submitPassword(event: FormEvent) {
    event.preventDefault();
    if (email.trim() === "" || password === "") {
      setError("Enter your work email and your password.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setChallenge(await startSignIn(email, password));
      setPassword("");
    } catch (caught) {
      setError(authErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function confirmPasskey() {
    if (!challenge) return;
    setBusy(true);
    setError(null);
    try {
      await finishSignIn(challenge);
    } catch (caught) {
      setError(authErrorMessage(caught));
      setBusy(false);
    }
  }

  function startAgain() {
    setChallenge(null);
    setError(null);
  }

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
        <div className="flex w-full max-w-[400px] flex-col">
          {challenge === null ? (
            <form onSubmit={(event) => void submitPassword(event)} noValidate className="flex flex-col">
              <p className="m-0 text-sm font-bold text-muted">Step 1 of 2</p>
              <h1 className="mt-2 mb-0 text-4xl leading-[1.1] font-extrabold tracking-[-0.8px]">Staff sign-in</h1>
              <p className="mt-2.5 mb-0 text-lg leading-normal text-muted">
                Sign in with your work email and password.
              </p>

              <div className="mt-5 flex flex-col gap-1.5">
                <Label htmlFor={emailId}>Work email</Label>
                <Input
                  id={emailId}
                  type="email"
                  autoComplete="username"
                  inputMode="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={error ? errorId : undefined}
                />
              </div>
              <div className="mt-3.5 flex flex-col gap-1.5">
                <Label htmlFor={passwordId}>Password</Label>
                <Input
                  id={passwordId}
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={error ? errorId : undefined}
                />
              </div>

              {error ? <ErrorNote id={errorId} message={error} /> : null}

              <div className="mt-4 flex items-start gap-3 rounded-xl border border-border bg-surface px-4 py-3.5">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-surface-sunken">
                  <KeyIcon />
                </span>
                <p className="m-0 text-sm leading-[1.45]">
                  <strong>Two steps, every time.</strong> After your password we confirm it is you with your passkey.
                  Staff cannot sign in with a texted code.
                </p>
              </div>

              <Button type="submit" size="lg" disabled={busy} className="mt-6">
                {busy ? "Checking" : "Continue"}
              </Button>
              <p className="mt-4 mb-0 text-center text-sm text-muted">
                Cannot get in? Ask an admin to reset your access.
              </p>
            </form>
          ) : (
            <div className="flex flex-col">
              <p className="m-0 text-sm font-bold text-muted">Step 2 of 2</p>
              <h1 className="mt-2 mb-0 text-4xl leading-[1.1] font-extrabold tracking-[-0.8px]">
                Confirm it is you
              </h1>
              <p className="mt-2.5 mb-0 text-lg leading-normal text-muted">
                Use the passkey on this device or your security key. Your browser will ask for your fingerprint, face
                or device PIN.
              </p>

              {error ? <ErrorNote id={errorId} message={error} /> : null}

              <Button size="lg" disabled={busy} onClick={() => void confirmPasskey()} className="mt-6">
                <KeyIcon />
                {busy ? "Waiting for your passkey" : "Use passkey"}
              </Button>
              <button
                type="button"
                onClick={startAgain}
                className="mt-2 h-11 cursor-pointer self-center text-md font-semibold text-ink underline"
              >
                Sign in as someone else
              </button>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

function ErrorNote({ id, message }: { id: string; message: string }) {
  return (
    <p id={id} role="alert" className="mt-4 mb-0 rounded-md bg-danger-surface px-3.5 py-3 text-md font-semibold text-danger">
      {message}
    </p>
  );
}

function KeyIcon() {
  return (
    <svg
      aria-hidden="true"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="8" cy="12" r="4" />
      <path d="M12 12h9M18 12v3M21 12v2" />
    </svg>
  );
}
