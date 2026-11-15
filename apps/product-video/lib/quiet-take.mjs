// A take that records nothing: the same moves as Take, quickly and off camera. Used to set up
// invented people and records through the real app by reusing the chapters' own steps.
import { Take } from "./take.mjs";

export class QuietTake extends Take {
  async start() {}
  async stop() {}
  async step() {}
  async snap() {}
  async sms() {}
  async zoom() {}
  async read() {}
  async glide() {}
  async scroll() {}
  wait(ms) {
    return new Promise((r) => setTimeout(r, Math.min(ms, 200)));
  }
  log() {}
}
