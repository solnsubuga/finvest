/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_STUB_API?: string;
  readonly VITE_DEPLOY_ENV?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
