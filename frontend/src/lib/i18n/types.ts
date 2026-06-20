export type Lang = 'es' | 'en';

// Interpolation params for a translation key: ``{var}`` placeholders are
// replaced by the matching entry. Values are stringified at substitution time.
export type TranslationParams = Record<string, string | number>;

// Translator function exposed by the i18n context. ``key`` is a dot path into
// the dictionary (e.g. ``'settings.title'``); ``params`` fills ``{var}`` slots.
export type Translate = (key: string, params?: TranslationParams) => string;
