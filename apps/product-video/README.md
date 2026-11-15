# Product video: Introducing Finvest

The scripts that record and edit the product video, on a local stack built from main. Nothing here runs in a deployment, and nothing reaches the internet: every public address is served on this machine.

The finished video is 18 chapters and an outro, about 25 minutes, 1920x1080 at 30 fps. It follows one continuous invented account: Esther Nabukenya, a Kampala teacher saving for her son's school fees, her brother Moses Okello, and staff Ruth Achieng (admin), Joel Mugisha (finance), Sarah Namatovu (admin) and Grace Atim (operations), all on `finvest-demo.test`.

## What is real and what stands in

Every screen, figure, email, PDF and report in the video comes from the app running on the demo data. The stand-ins are only the outside services:

| Service | In the video |
| --- | --- |
| ioTec and Pesapal | Their sandbox modes (`FINVEST_PAYMENTS_PROVIDER=sandbox`, `FINVEST_CARDS_PROVIDER=sandbox`) |
| Resend (email) | `resend.local.test` in `stack/front.mjs` keeps every email on disk |
| Mailbox | `mail.local.test` shows those emails. It hides one-time links printed as text, and opens PDF attachments with poppler after the password is typed |
| DigitalOcean Spaces | `storage.local.test`, a small S3 stand-in that keeps files on disk |
| ioTec Verify (phone names) | `verify.local.test` answers with the invented names, because Verify has no sandbox |
| SMS | The local console backend; the video shows each text as it was printed |
| Regula, Sigma360 | The development stubs. They are shown, not completed |
| The bank | Invented, marked SPECIMEN: the receipt Moses uploads (`lib/receipt.mjs`) and the trust account statement Joel imports |
| The paper register | `setup/paper-register/`, three invented clients for the migration trial |

The story clock moves with libfaketime (`stack/clock.sh`). It only goes forward, because a day the backend has closed stays closed.

## Running it

Everything lives in `$WORK` (`/tmp/finvest-product-video` by default, see `stack/env.sh`). The stack needs PostgreSQL 16, libfaketime, poppler-utils, ffmpeg, Python 3 with Pillow, and the Playwright Chromium.

```sh
setup/seed.sh              # a fresh stack a week before the story: Ruth and Joel, the specimen
                           # ID documents and past auction rates, then day 0 saved as "start"
node record.mjs 01 02 03 … 18
node edit.mjs 01 02 03 … 18
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
| 2 | Make it yours, and safe | Passkey, confirmed email | 0:34 |
| 3 | Prove who you are | ID check | 0:55 |
| 4 | Behind the counter: approving Esther | Ruth | 1:16 |
| 5 | Setting this week's rates | Joel, Ruth | 1:34 |
| 6 | The first investment | Esther | 0:50 |
| 7 | Saving for school fees | Esther | 1:02 |
| 8 | Investing on autopilot | Esther | 0:29 |
| 9 | Paying by bank transfer | Moses, Joel, Ruth | 2:09 |
| 10 | Buying the bills | Joel, Ruth | 1:43 |
| 11 | The same account on a laptop | Esther | 1:05 |
| 12 | Growing the team | Ruth, Sarah, Grace | 1:24 |
| 13 | Getting paid | Esther, Grace, Ruth | 1:24 |
| 14 | Sending and saving together | Esther, Moses | 1:08 |
| 15 | Help when she needs it | Esther, Grace, Moses, Ruth | 2:16 |
| 16 | Making it hers | Esther | 1:57 |
| 17 | Three months later | Ruth, Joel | 2:47 |
| 18 | School fees, ready | Esther, Ruth, a visitor | 1:32 |

## Rules the scripts keep

- Invented people, numbers and documents only. Passwords go into masked fields.
- No key, token or one-time link is shown. Links with tokens are made and opened off camera.
- Only the public addresses appear in the address bar.
- No result is faked: if a step fails, the take fails and is recorded again.
