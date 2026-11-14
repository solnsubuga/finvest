import { useCallback, useEffect, useState } from "react";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { isStubAuth } from "@/auth/index.ts";
import { Button } from "@/components/ui/button.tsx";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { createHttpDevicesApi, createStubDevicesApi, DevicesError, devicesErrorMessage, notConnectedDevicesApi, type Device, type DevicesApi, type SecondStep } from "./devices.ts";

const SUMMARY = "Every browser signed in as you. Sign out any you no longer use or do not recognise.";

const STEPS: Record<SecondStep, string> = {
  passkey: "a passkey",
  authenticator: "an authenticator app",
  code: "a text message code",
  signup: "a set-up link",
};

const TIME = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Africa/Kampala" });

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; devices: Device[] };

export function DevicesScreen({ api }: { api: DevicesApi }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchAll = useCallback(
    (): Promise<Load> =>
      api.list().then(
        (devices) => ({ state: "ready", devices }),
        (failure: unknown) => ({ state: "failed", message: devicesErrorMessage(failure) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchAll().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchAll]);

  async function run(key: string, command: () => Promise<string>) {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      setNotice(await command());
      setConfirming(false);
    } catch (failure) {
      setError(devicesErrorMessage(failure));
      if (!(failure instanceof DevicesError && failure.code === "not_found")) return;
    } finally {
      setBusy(null);
    }
    setLoad(await fetchAll());
  }

  return (
    <>
      <PageHeader title="Your devices" summary={SUMMARY} />
      {notice ? (
        <p role="status" className="m-0 max-w-[720px] rounded-md bg-success-surface px-3 py-2.5 text-sm font-semibold">
          {notice}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="m-0 max-w-[720px] rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold">
          {error}
        </p>
      ) : null}
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading your devices
        </p>
      ) : load.state === "failed" ? (
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">Your devices could not be loaded</h2>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button variant="secondary" onClick={() => void fetchAll().then(setLoad)}>
            Try again
          </Button>
        </section>
      ) : (
        <DeviceList
          devices={load.devices}
          busy={busy}
          confirming={confirming}
          onConfirm={setConfirming}
          onSignOut={(device) => void run(device.id, async () => (await api.signOut(device.id), `${device.device} is signed out.`))}
          onSignOutOthers={() =>
            void run("others", async () => {
              const ended = await api.signOutOthers();
              return ended === 1 ? "1 other device is signed out." : `${ended} other devices are signed out.`;
            })
          }
        />
      )}
    </>
  );
}

function DeviceList({
  devices,
  busy,
  confirming,
  onConfirm,
  onSignOut,
  onSignOutOthers,
}: {
  devices: readonly Device[];
  busy: string | null;
  confirming: boolean;
  onConfirm: (open: boolean) => void;
  onSignOut: (device: Device) => void;
  onSignOutOthers: () => void;
}) {
  const others = devices.filter((device) => !device.current);
  return (
    <div className="flex max-w-[720px] flex-col gap-4">
      <ul aria-label="Signed-in devices" className="m-0 flex list-none flex-col rounded-xl border border-border bg-surface px-4 py-1">
        {devices.map((device) => (
          <li key={device.id} className="flex flex-wrap items-center justify-between gap-3 border-b border-divider py-3.5 last:border-b-0">
            <div className="flex min-w-0 flex-col gap-0.5">
              <span className="flex flex-wrap items-center gap-2 text-md font-bold">
                {device.device}
                {device.current ? <span className="rounded-pill bg-lime px-2 py-0.5 text-2xs font-bold text-ink">This browser</span> : null}
              </span>
              <span className="text-sm text-muted">
                Signed in {TIME.format(new Date(device.signedInAt))} with {STEPS[device.secondStep]}. Last used {TIME.format(new Date(device.lastSeenAt))}.
              </span>
            </div>
            {device.current ? null : (
              <Button variant="secondary" disabled={busy !== null} onClick={() => onSignOut(device)}>
                {busy === device.id ? "Signing out" : "Sign out"}
              </Button>
            )}
          </li>
        ))}
      </ul>
      {others.length === 0 ? (
        <p className="m-0 text-md text-muted">You are signed in on this browser only.</p>
      ) : confirming ? (
        <section aria-label="Sign out every other device" className="flex flex-col gap-3 rounded-xl border-2 border-warning bg-surface p-[15px]">
          <p className="m-0 text-md leading-normal font-bold">
            Sign out {others.length === 1 ? "the other device" : `all ${others.length} other devices`}? Anyone using them must sign in again with your password and
            passkey or authenticator app.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button disabled={busy !== null} onClick={onSignOutOthers}>
              {busy === "others" ? "Signing out" : "Sign them out"}
            </Button>
            <Button variant="secondary" onClick={() => onConfirm(false)}>
              Cancel
            </Button>
          </div>
        </section>
      ) : (
        <div>
          <Button variant="secondary" disabled={busy !== null} onClick={() => onConfirm(true)}>
            Sign out every other device
          </Button>
        </div>
      )}
      <p className="m-0 text-sm leading-normal text-muted">
        To sign out this browser, use Sign Out in the menu. If you see a device you do not recognise, sign it out and tell an admin.
      </p>
    </div>
  );
}

function stubCase(): "sample" | "empty" | "error" {
  const sample = new URLSearchParams(window.location.search).get("sample");
  return sample === "empty" || sample === "error" ? sample : "sample";
}

export function DevicesPage() {
  // Sample devices under `pnpm dev` or a preview build, otherwise the real API.
  const [api] = useState<DevicesApi>(() => {
    if (isStubAuth()) return createStubDevicesApi(stubCase());
    const base = apiBaseUrl();
    return base ? createHttpDevicesApi(createHttp(base)) : notConnectedDevicesApi;
  });
  return <DevicesScreen api={api} />;
}
