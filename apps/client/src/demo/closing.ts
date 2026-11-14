/**
 * Demo only: whether the demo client has a request to close their account waiting. The demo
 * profile sets it, and the demo investing, goals and plans refuse new things while it is set,
 * as the backend does.
 */
let waiting = false;

export function setDemoClosing(value: boolean): void {
  waiting = value;
}

export function demoClosing(): boolean {
  return waiting;
}
