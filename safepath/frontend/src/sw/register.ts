/** Whether to register the service worker: production builds, or dev with VITE_ENABLE_SW=true. */
export function shouldRegisterServiceWorker(env: { PROD?: boolean; VITE_ENABLE_SW?: string }): boolean {
  return env.PROD === true || env.VITE_ENABLE_SW === 'true';
}

export function registerServiceWorker(): void {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return;
  if (!shouldRegisterServiceWorker(import.meta.env as { PROD?: boolean; VITE_ENABLE_SW?: string })) return;
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      /* the app works without a service worker; offline support is simply unavailable */
    });
  });
}
