# apps/client

The Finvest client app. One codebase for Android, iPhone and the web at app.finvestug.com.

**Status:** not scaffolded yet.

## Stack

React Native, Expo, TypeScript. Push notifications through Expo push. API types come from [`packages/api-client`](../../packages/api-client/README.md) and brand values from [`packages/tokens`](../../packages/tokens/README.md).

## Notes

- The web version ships at launch. About five screens have wide layouts (home, invest, portfolio, activity, documents). Everything else reuses the phone screen in a centred column.
- There is no wallet screen and no spendable balance.
- Clients never see tax rates, only net figures.
- The app PIN or fingerprint unlocks a phone that is already signed in and approves payments. It never signs in a new device.
- Build to the [design canvas](../../docs/design.md). Every PR that adds or changes a screen includes screenshots.
