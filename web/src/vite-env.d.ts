/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_FEED_REFRESH_S?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
