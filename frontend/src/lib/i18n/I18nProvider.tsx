import { useCallback, useMemo, useState, type ReactNode } from 'react';

import { I18nContext, type I18nState } from './I18nContext';
import { persistLang, resolveInitialLang } from './detect';
import { translate } from './translate';
import { dictionaries, FALLBACK_DICTIONARY } from './locales';
import type { Lang, TranslationParams } from './types';

type Props = { children: ReactNode };

/**
 * Provides the interface language and translator to the whole tree. The
 * provider lives in ``lib/i18n/`` (not ``app/providers/``) on purpose: it must
 * be importable by ``components/`` widgets too — and ``components/`` may not
 * import from ``app/``. See the i18n section of ``frontend_guide.md``.
 */
export default function I18nProvider({ children }: Props) {
  const [lang, setLangState] = useState<Lang>(resolveInitialLang);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    persistLang(next);
    // Keep the document language attribute in sync (a11y / browser hints).
    if (typeof document !== 'undefined') {
      document.documentElement.lang = next;
    }
  }, []);

  const value = useMemo<I18nState>(() => {
    const activeDict = dictionaries[lang];
    return {
      lang,
      setLang,
      t: (key: string, params?: TranslationParams) =>
        translate(activeDict, key, params, FALLBACK_DICTIONARY),
    };
  }, [lang, setLang]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
