import { Component, type ReactNode, Suspense } from "react";
import { PageHeader } from "@/pages/PageHeader.tsx";

/**
 * Each console screen is fetched the first time it is opened. While it arrives a short line
 * says so; if it cannot be fetched (the connection dropped, or a release replaced the file), the
 * screen says that and offers to load the page again, instead of a blank main area. Any other
 * error is thrown on, so a fault after a staff action is never reported as "nothing was changed".
 */
export function ScreenLoader({ children }: { children: ReactNode }) {
  return (
    <LoadFailedBoundary>
      <Suspense
        fallback={
          <div role="status" className="text-md text-muted">
            Opening the screen…
          </div>
        }
      >
        {children}
      </Suspense>
    </LoadFailedBoundary>
  );
}

/** True when the error is a screen file that could not be fetched, not a fault in the screen. */
export function isLoadError(error: unknown): boolean {
  const message =
    error instanceof Error ? `${error.name} ${error.message}` : String(error);
  return /failed to fetch dynamically imported module|error loading dynamically imported module|importing a module script failed|loading (css )?chunk|chunkloaderror/i.test(
    message,
  );
}

class LoadFailedBoundary extends Component<
  { children: ReactNode },
  { error: unknown }
> {
  state: { error: unknown } = { error: null };

  static getDerivedStateFromError(error: unknown): { error: unknown } {
    return { error };
  }

  render() {
    const { error } = this.state;
    if (error === null) return this.props.children;
    if (!isLoadError(error)) throw error;
    return (
      <>
        <PageHeader
          title="This screen did not load"
          summary="Part of the console did not arrive. Check the connection, then load the page again. Nothing was changed."
        />
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="self-start rounded-pill bg-black px-5 py-2.5 text-md font-bold text-white"
        >
          Load the page again
        </button>
      </>
    );
  }
}
