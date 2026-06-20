import { createContext, useContext } from 'react';

import type { Lang, Translate } from './types';

export type I18nState = {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: Translate;
};

export const I18nContext = createContext<I18nState | null>(null);

/**
 * Access the active translator and language. Mirrors ``useAuth``: throws when
 * called outside ``<I18nProvider>`` so a missing provider fails loudly instead
 * of silently rendering raw keys.
 */
export function useTranslation(): I18nState {
  const ctx = useContext(I18nContext);
  if (!ctx) {
    throw new Error('useTranslation must be used within I18nProvider');
  }
  return ctx;
}
