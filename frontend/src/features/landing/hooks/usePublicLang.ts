import { useCallback, useState } from 'react';

import { LANG_STORAGE_KEY, useTranslation } from '../../../lib/i18n';
import type { Lang } from '../../../lib/i18n';

/**
 * Resolve the initial language of the PUBLIC pages (landing / legal). Differs
 * from the app-wide ``resolveInitialLang`` in exactly one way: the
 * no-stored-choice fallback is biased to English — only an ``es-*`` browser
 * gets Spanish; every other locale (Google's reviewers browse in en-US) gets
 * English. The authenticated app keeps its own es-biased default untouched.
 */
function resolvePublicInitialLang(): Lang {
  try {
    const stored = window.localStorage.getItem(LANG_STORAGE_KEY);
    if (stored === 'es' || stored === 'en') return stored;
  } catch {
    // localStorage unavailable (private mode) — fall through to detection.
  }

  const navLang =
    typeof navigator !== 'undefined' && typeof navigator.language === 'string'
      ? navigator.language.toLowerCase()
      : '';
  return navLang.startsWith('es') ? 'es' : 'en';
}

/**
 * Language state for the public (unauthenticated) pages. A stored choice
 * always wins; otherwise the public English-biased detection above applies.
 * An explicit toggle writes through to the shared ``I18nProvider`` — which
 * persists ``localStorage['lang']`` and syncs ``document.documentElement.lang``
 * — so /login and the authenticated app follow the visitor's choice from that
 * point on. The detection itself never persists anything: a language the
 * visitor did not pick must not leak into the authenticated app's default.
 */
export default function usePublicLang(): { lang: Lang; setLang: (next: Lang) => void } {
  const { setLang: setAppLang } = useTranslation();
  const [lang, setLangState] = useState<Lang>(resolvePublicInitialLang);

  const setLang = useCallback(
    (next: Lang) => {
      setLangState(next);
      setAppLang(next);
    },
    [setAppLang],
  );

  return { lang, setLang };
}
