# Product video: Introducing Finvest

The scripts that record and edit the product video, on a local stack built from main. Nothing here runs in a deployment, and nothing reaches the internet: every public address is served on this machine.

The finished video is 21 chapters and an outro, about 30 minutes, 1920x1080 at 30 fps. It follows one continuous invented account: Esther Nabukenya, a Kampala teacher saving for her son's school fees, her brother Moses Okello, and staff Ruth Achieng (admin), Joel Mugisha (finance), Sarah Namatovu (admin) and Grace Atim (operations), all on `finvest-demo.test`.

## What is real and what stands in

Every screen, figure, email, PDF and report in the video comes from the app running on the demo data. The stand-ins are only the outside services:

| Service | In the video |
| --- | --- |
| ioTec and Pesapal | Their sandbox modes (`FINVEST_PAYMENTS_PROVIDER=sandbox`, `FINVEST_CARDS_PROVIDER=sandbox`). Pesapal's page is the app's own return page, which pays after a few seconds. A total ending 001 to 006 picks a failure |
| The bond | Invented: "2 Year Treasury Bond 2028", ISIN UG12K1502281, 14.25% coupon (`chapters/06-bond.mjs`). Not a real Bank of Uganda issue |
| Resend (email) | `resend.local.test` in `stack/front.mjs` keeps every email on disk |
| Mailbox | `mail.local.test` shows those emails. It hides one-time links printed as text, and opens PDF attachments with poppler after the password is typed |
| DigitalOcean Spaces | `storage.local.test`, a small S3 stand-in that keeps files on disk |
| ioTec Verify (phone names) | `verify.local.test` answers with the invented names, because Verify has no sandbox |
| SMS | The local console backend; the video shows each text as it was printed |
| Regula, Sigma360 | The development stubs. They are shown, not completed |
| The bank | Invented, marked SPECIMEN: the receipt Moses uploads (`lib/receipt.mjs`) and the trust account statement Joel imports |
| The paper register | `setup/paper-register/`, three invented clients for the migration trial |

The story clock moves with libfaketime (`stack/clock.sh`). It only goes forward, because a day the backend has closed stays closed. `$WORK/offset` is the story day counted from day 0, which is 15 Nov 2026 (`PV_DAY0` changes it). The clock adds the gap between day 0 and the real date, so a recording made on another day tells the same story.

## Running it

Run this only on a disposable machine or container, never on a laptop with a PostgreSQL you care about. `stack/up.sh` needs root: it appends the local addresses to `/etc/hosts`, runs commands as `su postgres`, and drops and recreates the `finvest_video` database.

Everything lives in `$WORK` (`/tmp/finvest-product-video` by default, see `stack/env.sh`). The stack needs PostgreSQL 16, libfaketime, poppler-utils, ffmpeg, Python 3 with Pillow, numpy and mido, the Playwright Chromium, and for the music FluidSynth with the FluidR3 General MIDI sound font (`fluidsynth` and `fluid-soundfont-gm`). The music is written for this video by `edit/music.py`; without FluidSynth the edit falls back to a plain sine-tone pad.

```sh
setup/seed.sh              # a fresh stack a week before the story: Ruth and Joel, the specimen
                           # ID documents and past auction rates, then day 0 saved as "start"
node record.mjs 01 02 03 … 21
node edit.mjs 01 02 03 … 21
node edit.mjs --final      # introducing-finvest.mp4 and introducing-finvest-no-music.mp4 in $WORK/out
```

`record.mjs` saves a backup after every chapter (`after-NN`): the database, stored files, emails, the clock, and the browsers' sign-ins and passkeys. To record one chapter again, put the story back to where it starts:

```sh
node record.mjs 14 --restore     # restores after-13, then records 14
PV_DRY=1 node record.mjs 14 --restore   # the same, without capturing frames
```

## Chapters

| # | Title | Who | Length |
| --- | --- | --- | --- |
| 1 | Finvest | Website, sign-up | 0:59 |
| 2 | Make it yours, and safe | Passkey, confirmed email | 0:35 |
| 3 | Prove who you are | ID check | 0:55 |
| 4 | Behind the counter: approving Esther | Ruth | 1:14 |
| 5 | Setting this week's rates | Joel, Ruth | 1:36 |
| 6 | A bond for the long run | Joel, Ruth, Esther | 1:25 |
| 7 | The first investment | Esther | 0:53 |
| 8 | Saving for school fees | Esther | 0:59 |
| 9 | Investing on autopilot | Esther | 0:29 |
| 10 | A bond, paid by card | Esther | 1:05 |
| 11 | Paying by bank transfer | Moses, Joel, Ruth | 2:09 |
| 12 | Buying the bills | Joel, Ruth | 1:43 |
| 13 | The same account on a laptop | Esther | 1:05 |
| 14 | Growing the team | Ruth, Sarah, Grace | 1:53 |
| 15 | Getting paid | Esther, Grace, Ruth | 1:26 |
| 16 | Sending and saving together | Esther, Moses | 1:35 |
| 17 | Help when she needs it | Esther, Grace, Moses, Ruth | 2:14 |
| 18 | Making it hers | Esther | 2:05 |
| 19 | Three months later | Ruth, Joel | 2:53 |
| 20 | School fees, ready | Esther, Joel, Ruth | 1:45 |
| 21 | Proof on paper | Esther, a visitor | 0:54 |

## Rules the scripts keep

- Invented people, numbers and documents only. Passwords go into masked fields.
- No key, token or one-time link is shown. Links with tokens are made and opened off camera.
- Only the public addresses appear in the address bar.
- No result is faked: if a step fails, the take fails and is recorded again.
