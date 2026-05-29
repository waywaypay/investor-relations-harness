/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ATTEST_API?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
