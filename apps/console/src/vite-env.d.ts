/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_STUB_API?: string;
  readonly VITE_DEPLOY_ENV?: string;
  /** The API's origin, such as https://api.finvestug.com. */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
