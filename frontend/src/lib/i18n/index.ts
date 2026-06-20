export { default as I18nProvider } from './I18nProvider';
export { I18nContext, useTranslation, type I18nState } from './I18nContext';
export { translate } from './translate';
export { resolveInitialLang, persistLang, LANG_STORAGE_KEY } from './detect';
export type { Lang, Translate, TranslationParams } from './types';
