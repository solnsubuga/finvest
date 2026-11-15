// Metro's settings for the client app: Expo's defaults, plus one change to what is bundled.
const path = require("node:path");
const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);

// Zod (through @finvest/api-client) exports every language it can write its error messages in,
// and Metro bundles everything a file exports, so the start-up script carried about 290 KB of
// translations the app never shows: it shows its own wording, and Zod's English is the only
// default. Zod's own index of languages is swapped for one that has English alone
// (src/api/zodLocales.js).
const ZOD_LOCALES = /[\\/]zod[\\/]v4[\\/](classic|core)[\\/][^\\/]+\.js$/;
const englishOnly = path.join(__dirname, "src", "api", "zodLocales.js");
const resolve = config.resolver.resolveRequest;
config.resolver.resolveRequest = (context, moduleName, platform) => {
  if (moduleName === "../locales/index.js" && ZOD_LOCALES.test(context.originModulePath)) {
    return { type: "sourceFile", filePath: englishOnly };
  }
  return (resolve ?? context.resolveRequest)(context, moduleName, platform);
};

module.exports = config;
