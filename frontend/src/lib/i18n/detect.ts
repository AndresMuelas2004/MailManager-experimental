import type { Lang } from './types';

export const LANG_STORAGE_KEY = 'lang';

function isLang(value: string | null): value is Lang {
  return value === 'es' || value === 'en';
}

/**
 * Resolve the interface language to use on first paint. Impure by design: it
 * reads ``localStorage`` (the persisted choice) and ``navigator.language`` (the
 * first-time guess). A stored value always wins; otherwise English is picked
 * only when the browser language starts with ``en`` — every other locale,
 * including the unknown case, defaults to Spanish.
 */
export function resolveInitialLang(): Lang {
  try {
    const stored = window.localStorage.getItem(LANG_STORAGE_KEY);
    if (isLang(stored)) return stored;
  } catch {
    // localStorage unavailable (private mode / SSR) — fall back to detection.
  }

  const navLang =
    typeof navigator !== 'undefined' && typeof navigator.language === 'string'
      ? navigator.language.toLowerCase()
      : '';
  if (navLang.startsWith('en')) return 'en';
  return 'es';
}

/**
 * Persist the chosen language. Impure (writes ``localStorage``); swallows
 * failures so a storage error never breaks the language switch in the UI.
 */
export function persistLang(lang: Lang): void {
  try {
    window.localStorage.setItem(LANG_STORAGE_KEY, lang);
  } catch {
    // localStorage unavailable — the in-memory choice still applies this session.
  }
}
