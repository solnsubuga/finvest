/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_STUB_API?: string;
  readonly VITE_DEPLOY_ENV?: string;
  /** The API's origin, such as http://localhost:8000. Deployed builds use their own address. */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
