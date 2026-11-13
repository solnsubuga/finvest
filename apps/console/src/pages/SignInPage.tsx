import { useId, useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router";
import { AuthError, authErrorMessage, type SecondStepMethod, type SignInChallenge } from "@/auth/api.ts";
import { useAuth } from "@/auth/context.ts";
import { AuthLayout } from "@/components/AuthLayout.tsx";
import { ErrorNote, KeyIcon } from "@/components/AuthParts.tsx";
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
  const codeId = useId();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState<SignInChallenge | null>(null);
  const [method, setMethod] = useState<SecondStepMethod>("passkey");
  const [code, setCode] = useState("");
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
      const next = await startSignIn(email, password);
      if (next.methods.length === 0) {
        // A second step is required for every staff account. Never go on without one.
        throw new AuthError("second_step_missing");
      }
      setChallenge(next);
      // Offer the passkey first when the person has one.
      setMethod(next.methods.includes("passkey") ? "passkey" : "authenticator");
      setCode("");
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
      await finishSignIn(challenge, { method: "passkey" });
    } catch (caught) {
      setError(authErrorMessage(caught));
      setBusy(false);
    }
  }

  async function submitCode(event: FormEvent) {
    event.preventDefault();
    if (!challenge) return;
    if (!/^\d{6}$/.test(code)) {
      setError("Enter the 6-digit code from your authenticator app.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await finishSignIn(challenge, { method: "authenticator", code });
    } catch (caught) {
      setError(authErrorMessage(caught));
      setCode("");
      setBusy(false);
    }
  }

  function switchMethod(next: SecondStepMethod) {
    setMethod(next);
    setCode("");
    setError(null);
  }

  function startAgain() {
    setChallenge(null);
    setError(null);
  }

  return (
    <AuthLayout>
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
              <strong>Two steps, every time.</strong> After your password we confirm it is you with your
              passkey or a code from your authenticator app. Staff cannot sign in with a texted code.
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

          {method === "passkey" ? (
            <>
              <p className="mt-2.5 mb-0 text-lg leading-normal text-muted">
                Use the passkey on this device or your security key. Your browser will ask for your
                fingerprint, face or device PIN.
              </p>
              {error ? <ErrorNote id={errorId} message={error} /> : null}
              <Button size="lg" disabled={busy} onClick={() => void confirmPasskey()} className="mt-6">
                <KeyIcon />
                {busy ? "Waiting for your passkey" : "Use passkey"}
              </Button>
            </>
          ) : (
            <form onSubmit={(event) => void submitCode(event)} noValidate className="flex flex-col">
              <p className="mt-2.5 mb-0 text-lg leading-normal text-muted">
                Open your authenticator app and enter the 6-digit code for Finvest.
              </p>
              <div className="mt-5 flex flex-col gap-1.5">
                <Label htmlFor={codeId}>6-digit code</Label>
                <Input
                  id={codeId}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  autoFocus
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={error ? errorId : undefined}
                  className="text-2xl tracking-[0.3em] tabular-nums"
                />
              </div>
              {error ? <ErrorNote id={errorId} message={error} /> : null}
              <Button type="submit" size="lg" disabled={busy} className="mt-6">
                {busy ? "Checking" : "Sign in"}
              </Button>
            </form>
          )}

          {challenge.methods.length > 1 ? (
            <button
              type="button"
              onClick={() => switchMethod(method === "passkey" ? "authenticator" : "passkey")}
              className="mt-2 h-11 cursor-pointer self-center text-md font-semibold text-ink underline"
            >
              {method === "passkey" ? "Use a code from your authenticator app" : "Use your passkey"}
            </button>
          ) : null}
          <button
            type="button"
            onClick={startAgain}
            className="h-11 cursor-pointer self-center text-md font-semibold text-ink underline"
          >
            Sign in as someone else
          </button>
        </div>
      )}
    </AuthLayout>
  );
}
