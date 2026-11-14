/**
 * The statement check form. It reads the code typed, asks the API, and shows what Finvest's
 * records say. See lib/check.ts.
 */

import { describeChecked, normaliseCode, parseCheck, showCode, type CheckAnswer } from "../lib/check.ts";

const TIMEOUT_MS = 8000;

async function ask(endpoint: string, code: string): Promise<CheckAnswer | null> {
  try {
    const response = await fetch(`${endpoint}/${encodeURIComponent(code)}`, {
      credentials: "omit",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!response.ok) return null;
    return parseCheck(await response.json());
  } catch {
    return null;
  }
}

function start(form: HTMLFormElement): void {
  const endpoint = form.dataset["checkUrl"];
  const input = form.querySelector<HTMLInputElement>('input[name="code"]');
  const error = form.querySelector<HTMLElement>("[data-check-error]");
  const button = form.querySelector<HTMLButtonElement>('button[type="submit"]');
  const page = form.closest("[data-check-page]");
  const results = {
    genuine: page?.querySelector<HTMLElement>("[data-check-genuine]"),
    notFound: page?.querySelector<HTMLElement>("[data-check-not-found]"),
    unavailable: page?.querySelector<HTMLElement>("[data-check-unavailable]"),
    tooMany: page?.querySelector<HTMLElement>("[data-check-too-many]"),
  };
  const facts = page?.querySelector<HTMLElement>("[data-check-facts]");
  const checkedCode = page?.querySelectorAll<HTMLElement>("[data-check-code]");
  if (!endpoint || !input || !error || !button || !facts || !checkedCode) return;
  if (Object.values(results).some((element) => !element)) return;

  const show = (which: keyof typeof results | null): void => {
    for (const [name, element] of Object.entries(results)) {
      if (element) element.hidden = name !== which;
    }
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const code = normaliseCode(input.value);
    show(null);
    if (code === null) {
      error.textContent = "Enter the 12 letters and numbers shown as the check code, for example K7Q2-9XMA-4TPD.";
      error.hidden = false;
      input.setAttribute("aria-invalid", "true");
      input.focus();
      return;
    }
    error.hidden = true;
    input.removeAttribute("aria-invalid");
    button.disabled = true;
    button.textContent = "Checking";
    void ask(endpoint, code).then((answer) => {
      button.disabled = false;
      button.textContent = "Check";
      checkedCode.forEach((element) => (element.textContent = showCode(code)));
      if (answer === null) return show("unavailable");
      if (answer.result === "not_found") return show("notFound");
      if (answer.result === "too_many_tries") return show("tooMany");
      facts.replaceChildren(
        ...describeChecked(answer.statement).flatMap(([label, value]) => {
          const term = document.createElement("dt");
          term.textContent = label;
          const detail = document.createElement("dd");
          detail.textContent = value;
          return [term, detail];
        }),
      );
      show("genuine");
    });
  });
}

export function startChecks(): void {
  document.querySelectorAll<HTMLFormElement>("form[data-check]").forEach(start);
}
