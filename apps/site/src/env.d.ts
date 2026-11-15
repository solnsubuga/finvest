interface ImportMetaEnv {
  readonly PUBLIC_API_URL?: string;
  readonly PUBLIC_APP_URL?: string;
  readonly PUBLIC_SITE_URL?: string;
  readonly PUBLIC_INDEXABLE?: string;
  readonly PUBLIC_APPLE_TEAM_ID?: string;
  readonly PUBLIC_ANDROID_CERT_SHA256?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
