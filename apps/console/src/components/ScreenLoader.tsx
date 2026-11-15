import { Component, type ReactNode, Suspense } from "react";
import { PageHeader } from "@/pages/PageHeader.tsx";

/**
 * Each console screen is fetched the first time it is opened. While it arrives a short line
 * says so; if it cannot be fetched (the connection dropped, or a release replaced the file), the
 * screen says that and offers to load the page again, instead of a blank main area.
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

class LoadFailedBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
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
