// Google Analytics 4 wiring, kept behind explicit consent.
//
// The three impure helpers below (localStorage, injecting a <script>, pushing to
// window.dataLayer) live here because two unrelated callers need them: the
// consent banner in components/ui and the analytics gate in app/layout.
//
// Two deliberate scope decisions are enforced from this module:
//   1. Nothing loads until the visitor accepts. `loadAnalytics` is only ever
//      called after a 'granted' consent, so a visitor who rejects (or has not
//      answered yet) never downloads Google's script and gets no cookies.
//   2. Only public pages are measured. `isTrackedPath` is the single source of
//      truth for that list, and `config` is sent with `send_page_view: false`
//      so GA never auto-reports a route on its own — every page_view this app
//      sends is an explicit `trackPageView` call for a path that passed the
//      check. (The matching "page changes based on browser history events"
//      toggle is also OFF in the GA property's enhanced measurement, which is
//      what stops gtag.js from reporting in-app routes by itself once loaded.)

declare global {
  interface Window {
    dataLayer?: unknown[];
    gtag?: (...args: unknown[]) => void;
  }
}

export type AnalyticsConsent = 'granted' | 'denied';

export const CONSENT_STORAGE_KEY = 'analyticsConsent';

// Baked into the bundle at build time by Vite. Empty (the default in dev and in
// any deployment that does not set it) disables analytics entirely: no banner,
// no script, no events.
export const MEASUREMENT_ID: string = (
  (import.meta.env.VITE_GA_MEASUREMENT_ID as string | undefined) ?? ''
).trim();

export function isAnalyticsConfigured(): boolean {
  return MEASUREMENT_ID.length > 0;
}

// The only measured surface: the public pages an anonymous visitor can reach.
// Everything behind the login (/home, /m/:mailboxId/**) is deliberately absent
// so no mailbox route is ever sent to Google.
const TRACKED_PATHS = new Set(['/', '/privacy', '/terms', '/login']);

/** True when ``pathname`` is one of the public pages we measure. Pure. */
export function isTrackedPath(pathname: string): boolean {
  // Trailing slashes are normalised so '/privacy/' matches '/privacy'.
  const normalised = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname;
  return TRACKED_PATHS.has(normalised === '' ? '/' : normalised);
}

/**
 * Read the stored consent choice; null when the visitor has not answered yet,
 * the value is unrecognised, or storage is unavailable (private mode). Impure
 * (reads localStorage). The try/catch mirrors lib/lastSync.ts.
 */
export function readAnalyticsConsent(): AnalyticsConsent | null {
  try {
    const raw = window.localStorage.getItem(CONSENT_STORAGE_KEY);
    return raw === 'granted' || raw === 'denied' ? raw : null;
  } catch {
    return null;
  }
}

/**
 * Persist the visitor's choice. Impure (writes localStorage); swallows storage
 * errors so a quota / private-mode failure never breaks the banner — the choice
 * still holds for the current session via in-memory state.
 */
export function writeAnalyticsConsent(consent: AnalyticsConsent): void {
  try {
    window.localStorage.setItem(CONSENT_STORAGE_KEY, consent);
  } catch {
    // localStorage unavailable — the in-memory state still holds this session.
  }
}

/**
 * Inject gtag.js and configure the property. Idempotent: repeated calls (route
 * changes, re-renders) are no-ops once the tag is present. Impure (touches the
 * DOM). No-op when no measurement id was baked into the bundle.
 */
export function loadAnalytics(): void {
  if (!isAnalyticsConfigured()) return;
  if (window.gtag) return;

  const script = document.createElement('script');
  script.async = true;
  script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(MEASUREMENT_ID)}`;
  document.head.appendChild(script);

  window.dataLayer = window.dataLayer ?? [];
  // `function` + `arguments` es load-bearing y NO puede reescribirse como una
  // arrow con rest params: gtag.js reconoce sus comandos porque el elemento
  // empujado es un objeto `arguments`, y descarta en silencio los arrays
  // normales. Con un array la cola crece, pero la propiedad nunca se configura
  // — sin cookie `_ga` y sin un solo hit enviado, sin ningún error visible.
  // Los parámetros se declaran solo para tipar las llamadas de abajo; el cuerpo
  // empuja `arguments`, que es lo que gtag.js sabe interpretar.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  function gtag(..._args: unknown[]): void {
    // eslint-disable-next-line prefer-rest-params
    window.dataLayer?.push(arguments);
  }
  window.gtag = gtag as Window['gtag'];

  gtag('js', new Date());
  // send_page_view:false is load-bearing — see the header comment. Without it
  // gtag reports the current route the moment it loads, which on an accepted
  // consent given inside the app would leak a mailbox path.
  gtag('config', MEASUREMENT_ID, { send_page_view: false });
}

/**
 * Report one page view for ``path``. Silently ignored when analytics is not
 * loaded (no consent, no measurement id) or when the path is not a public page.
 */
export function trackPageView(path: string): void {
  if (!window.gtag) return;
  if (!isTrackedPath(path)) return;
  window.gtag('event', 'page_view', {
    page_path: path,
    page_location: window.location.origin + path,
    page_title: document.title,
  });
}
