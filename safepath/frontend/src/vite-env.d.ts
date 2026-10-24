/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  readonly VITE_REALTIME_MODE: 'appsync' | 'websocket' | 'sse';
}
