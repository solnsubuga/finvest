import { QRCodeSVG } from "qrcode.react";
import { useEffect, useId, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import {
  AuthError,
  authErrorMessage,
  PASSWORD_MIN_LENGTH,
  type AuthenticatorSetup,
  type Enrolment,
  type SecondStepMethod,
} from "@/auth/api.ts";
import { useAuth } from "@/auth/context.ts";
import { ROLE_LABELS } from "@/auth/types.ts";
import { AuthLayout } from "@/components/AuthLayout.tsx";
import { ErrorNote, KeyIcon } from "@/components/AuthParts.tsx";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { cn } from "@/lib.ts";

const METHOD_COPY: Record<SecondStepMethod, { title: string; detail: string }> = {
  passkey: {
    title: "Passkey",
    detail: "Your fingerprint, face or device PIN on this device, or a security key.",
  },
  authenticator: {
    title: "Authenticator app",
    detail: "A 6-digit code from an authenticator app on your phone.",
  },
};

type LinkState =
  | { status: "checking" }
  | { status: "ready"; enrolment: Enrolment }
  | { status: "failed"; message: string };

/** "ABCDEFGH" becomes "ABCD EFGH", which is easier to type by hand. */
function grouped(secret: string): string {
  return secret.match(/.{1,4}/g)?.join(" ") ?? secret;
}

/**
 * First-time set-up from a one-time link: /enrol#<token>.
 *
 * The token sits after the # so that it is never sent to a server in an
 * address or kept in server logs. It is read once and then removed from the
 * address bar.
 */
export function EnrolPage() {
  const { getEnrolment, enrolWithPasskey, startAuthenticatorEnrolment, enrolWithAuthenticator } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const passwordId = useId();
  const confirmId = useId();
  const codeId = useId();
  const errorId = useId();
  const hintId = useId();

  const [token] = useState(() => location.hash.replace(/^#/, ""));
  const [link, setLink] = useState<LinkState>(
    token === ""
      ? { status: "failed", message: authErrorMessage(new AuthError("link_expired")) }
      : { status: "checking" },
  );
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [method, setMethod] = useState<SecondStepMethod | null>(null);
  const [setup, setSetup] = useState<AuthenticatorSetup | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (location.hash !== "") {
      void navigate(location.pathname, { replace: true });
    }
  }, [location.hash, location.pathname, navigate]);

  useEffect(() => {
    if (token === "") return;
    let cancelled = false;
    getEnrolment(token).then(
      (enrolment) => {
        if (cancelled) return;
        setLink({ status: "ready", enrolment });
        // With one choice there is nothing to choose.
        const only = enrolment.methods.length === 1 ? enrolment.methods[0] : undefined;
        if (!only) return;
        setMethod(only);
        if (only === "authenticator") {
          startAuthenticatorEnrolment(token).then(
            (next) => {
              if (!cancelled) setSetup(next);
            },
            (caught: unknown) => {
              if (!cancelled) setError(authErrorMessage(caught));
            },
          );
        }
      },
      (caught: unknown) => {
        if (!cancelled) setLink({ status: "failed", message: authErrorMessage(caught) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [token, getEnrolment, startAuthenticatorEnrolment]);

  if (link.status === "checking") {
    return (
      <AuthLayout>
        <p role="status" className="m-0 text-lg text-muted">
          Checking your set-up link
        </p>
      </AuthLayout>
    );
  }

  if (link.status === "failed") {
    return (
      <AuthLayout>
        <h1 className="m-0 text-4xl leading-[1.1] font-extrabold tracking-[-0.8px]">
          This link cannot be used
        </h1>
        <ErrorNote id={errorId} message={link.message} />
        <Link
          to="/sign-in"
          className="mt-4 flex h-11 items-center self-start text-md font-semibold text-ink underline"
        >
          Go to sign-in
        </Link>
      </AuthLayout>
    );
  }

  const { enrolment } = link;

  function passwordProblem(): string | null {
    if (password.length < PASSWORD_MIN_LENGTH) {
      return authErrorMessage(new AuthError("password_too_short"));
    }
    if (password !== confirm) return "The two passwords do not match.";
    return null;
  }

  async function chooseMethod(next: SecondStepMethod) {
    setMethod(next);
    setError(null);
    if (next !== "authenticator" || setup) return;
    try {
      setSetup(await startAuthenticatorEnrolment(token));
    } catch (caught) {
      setError(authErrorMessage(caught));
      setMethod(null);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const problem = passwordProblem();
    if (problem) {
      setError(problem);
      return;
    }
    if (method === null) {
      setError("Choose a passkey or an authenticator app.");
      return;
    }
    if (method === "authenticator" && !/^\d{6}$/.test(code)) {
      setError("Enter the 6-digit code from your authenticator app.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (method === "passkey") await enrolWithPasskey(token, password);
      else await enrolWithAuthenticator(token, password, code);
      await navigate("/", { replace: true });
    } catch (caught) {
      setError(authErrorMessage(caught));
      setCode("");
      setBusy(false);
    }
  }

  return (
    <AuthLayout>
      <form onSubmit={(event) => void submit(event)} noValidate className="flex flex-col">
        <h1 className="m-0 text-4xl leading-[1.1] font-extrabold tracking-[-0.8px]">Set up your account</h1>
        <p className="mt-2.5 mb-0 text-lg leading-normal text-muted">
          Welcome, {enrolment.name}. You are joining as {ROLE_LABELS[enrolment.role]} with {enrolment.email}.
        </p>

        <h2 className="mt-6 mb-0 text-lg font-extrabold">1. Create a password</h2>
        <div className="mt-3 flex flex-col gap-1.5">
          <Label htmlFor={passwordId}>Password</Label>
          <Input
            id={passwordId}
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-describedby={hintId}
          />
          <p id={hintId} className="m-0 text-sm text-muted">
            At least {PASSWORD_MIN_LENGTH} characters. A short sentence works well.
          </p>
        </div>
        <div className="mt-3.5 flex flex-col gap-1.5">
          <Label htmlFor={confirmId}>Type it again</Label>
          <Input
            id={confirmId}
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
        </div>

        <fieldset className="m-0 mt-6 flex min-w-0 flex-col gap-2 border-0 p-0">
          <legend className="mb-1 p-0 text-lg font-extrabold">2. Choose your second step</legend>
          <p className="m-0 text-sm leading-normal text-muted">
            Every member of staff needs one. You will use it each time you sign in, after your password.
          </p>
          {enrolment.methods.map((option) => (
            <label
              key={option}
              className={cn(
                "flex cursor-pointer items-start gap-3 rounded-lg border bg-surface px-4 py-3.5",
                method === option ? "border-black outline outline-1 outline-black" : "border-border-strong",
              )}
            >
              <input
                type="radio"
                name="second-step"
                value={option}
                checked={method === option}
                onChange={() => void chooseMethod(option)}
                className="mt-1 size-4 accent-black"
              />
              <span className="flex flex-col">
                <span className="text-md font-bold">{METHOD_COPY[option].title}</span>
                <span className="text-sm leading-normal text-muted">{METHOD_COPY[option].detail}</span>
              </span>
            </label>
          ))}
        </fieldset>

        {method === "authenticator" && setup ? (
          <div className="mt-4 flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
            <p className="m-0 text-sm leading-normal">
              <strong>Scan this with your authenticator app</strong>, then enter the 6-digit code it shows.
            </p>
            <div className="self-center rounded-md bg-white p-2">
              <QRCodeSVG value={setup.uri} size={168} title="QR code for your authenticator app" />
            </div>
            <p className="m-0 text-sm leading-normal text-muted">
              Cannot scan it? Type this key into the app:
              <span className="mt-1 block font-mono text-sm font-semibold break-all text-ink">
                {grouped(setup.secret)}
              </span>
            </p>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={codeId}>6-digit code</Label>
              <Input
                id={codeId}
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                value={code}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
                className="text-2xl tracking-[0.3em] tabular-nums"
              />
            </div>
          </div>
        ) : null}

        {error ? <ErrorNote id={errorId} message={error} /> : null}

        <Button type="submit" size="lg" disabled={busy} className="mt-6">
          {method === "passkey" ? <KeyIcon /> : null}
          {busy ? "Setting up" : method === "passkey" ? "Create passkey and finish" : "Finish set-up"}
        </Button>
      </form>
    </AuthLayout>
  );
}
