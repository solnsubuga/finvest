import { screen } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

/**
 * Renders the router once and throws the render away.
 *
 * The first render in a test file loads every route from cold, which can take several
 * seconds on a busy CI runner. Doing that here, in a `beforeAll` with its own time limit,
 * lets each test keep Jest's normal 5 second limit for the flow it checks.
 */
export async function warmRouter(): Promise<void> {
  const view = await renderRouter("./src/app", { initialUrl: "/" });
  await screen.findByText("Log in", {}, { timeout: 25_000 });
  view.unmount();
}

/** Time allowed for `warmRouter`. */
export const WARM_ROUTER_MS = 30_000;
