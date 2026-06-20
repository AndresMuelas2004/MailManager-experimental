import { afterEach, beforeEach } from 'vitest';

import { LANG_STORAGE_KEY } from '../lib/i18n';
import type { Lang } from '../lib/i18n';

/**
 * Pin the UI language for a test file.
 *
 * The real `I18nProvider` (wired into `renderWithProviders`) resolves its
 * initial language from `localStorage['lang']`, falling back to the browser
 * language — which is English in jsdom. Tests that assert fixed Spanish copy
 * must therefore seed the stored language before rendering. Call this once at
 * the top level of a spec file; it registers the seed/cleanup hooks so the
 * choice does not leak into other files.
 *
 * NB: intentionally NOT named ``useTestLang`` — it is not a React hook (it
 * only registers Vitest lifecycle hooks), and a ``use`` prefix would trip
 * ``react-hooks/rules-of-hooks`` when called at a module's top level.
 */
export function pinTestLang(lang: Lang = 'es'): void {
  beforeEach(() => {
    window.localStorage.setItem(LANG_STORAGE_KEY, lang);
  });
  afterEach(() => {
    window.localStorage.removeItem(LANG_STORAGE_KEY);
  });
}
