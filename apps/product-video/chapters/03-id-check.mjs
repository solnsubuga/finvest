import path from "node:path";
import { WORK } from "../lib/browser.mjs";
import { CLIENT } from "../lib/people.mjs";

const doc = (f) => path.join(WORK, CLIENT.documents, f);
const visible = (page, id) => page.getByTestId(id).locator("visible=true");

async function choosePhoto(t, page, file) {
  const chooser = page.waitForEvent("filechooser");
  await t.click(visible(page, "photo-choose"));
  await (await chooser).setFiles(file);
  await visible(page, "photo-use").waitFor();
}

export default {
  id: "03",
  title: "Prove who you are",
  subtitle: "A quick ID check, done once.",
  steps: [
    { caption: "The law asks Finvest to confirm who she is", does: "Home, tap Confirm who you are" },
    { caption: "She picks her country and her National ID", does: "Uganda, National ID, Continue" },
    { caption: "A photo of the front, checked before it is sent", does: "Choose a photo: id-front.jpg (an invented specimen), read the checks, Use this photo" },
    { caption: "Then the back of the card", does: "Choose a photo: id-back.jpg, Use this photo" },
    { caption: "She types her details exactly as printed", does: `NABUKENYA, Esther, ${CLIENT.nin}, 14/03/1991, ${CLIENT.idExpires}` },
    { caption: "A live selfie shows a real person is there", does: "Start live check, Open the live check (the identity partner's page), Live selfie done" },
    { caption: "She hands in her check for a person to review", does: "Hand in my ID check, read the status" },
  ],
  takes: [{ name: "check", who: "esther-phone", layout: "phone" }],
  async check(t, page) {
    await page.goto("https://app.finvestug.com/");
    await page.getByTestId("set-up-id").waitFor();
    await t.start();
    await t.step("The law asks Finvest to confirm who she is", { hold: 1200 });
    await t.click(page.getByTestId("set-up-id"));
    await t.step("She picks her country and her National ID", { hold: 800 });
    await t.click(page.getByTestId("country-UG"));
    await t.click(page.getByTestId("document-national_id"));
    await t.click(page.getByTestId("document-continue"));
    await t.step("A photo of the front, checked before it is sent", { hold: 900 });
    await choosePhoto(t, page, doc("id-front.jpg"));
    await t.read(2600);
    await t.click(visible(page, "photo-use"));
    await t.step("Then the back of the card", { hold: 600 });
    await choosePhoto(t, page, doc("id-back.jpg"));
    await t.read(1400);
    await t.click(visible(page, "photo-use"));
    await page.getByTestId("details-surname").waitFor();
    await t.step("She types her details exactly as printed", { hold: 700 });
    await t.type(page.getByTestId("details-surname"), CLIENT.family.toUpperCase());
    await t.type(page.getByTestId("details-given-names"), CLIENT.given);
    await t.type(page.getByTestId("details-number"), CLIENT.nin);
    await t.type(page.getByTestId("details-born"), `${CLIENT.birth.day}/${CLIENT.birth.month}/${CLIENT.birth.year}`);
    await t.type(page.getByTestId("details-expires"), CLIENT.idExpires);
    await t.read(1000);
    await t.click(page.getByTestId("details-save"));
    await page.getByTestId("live-start").waitFor();
    await t.step("A live selfie shows a real person is there", { hold: 1200 });
    await t.read(1500);
    await t.click(page.getByTestId("live-start"));
    await page.getByTestId("live-open").waitFor();
    await t.read(1600);
    // The live check opens the identity partner's own page in a new tab. Locally it is the
    // development stand-in, which passes on its own, so that tab is closed off camera.
    const popup = page.context().waitForEvent("page", { timeout: 8000 }).catch(() => null);
    await t.click(page.getByTestId("live-open"));
    await (await popup)?.close().catch(() => {});
    // Coming back to the app's tab, as a person does, is what makes it look for the result.
    await page.bringToFront();
    await t.cut(async () => {
      try {
        await page.getByTestId("live-continue").waitFor({ timeout: 25000 });
      } catch {
        // The screen reads the result every few seconds. If it has not moved on, opening it
        // again reads at once.
        await page.reload();
        await page.getByTestId("live-continue").waitFor({ timeout: 60000 });
      }
    });
    await t.read(2000);
    await t.step("She hands in her check for a person to review", { hold: 600 });
    await t.click(page.getByTestId("live-continue"));
    await page.getByTestId("id-check-home").waitFor();
    await t.read(3500);
    await t.stop();
  },
};
