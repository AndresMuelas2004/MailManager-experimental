import type { TranslationParams } from './types';

// A dictionary is an arbitrarily nested object of string leaves. Kept loose
// here so ``translate`` stays generic; the concrete ``es`` dictionary pins the
// canonical shape and ``en`` is typed against it (see locales/).
export type Dictionary = { [key: string]: string | Dictionary };

function resolvePath(dict: Dictionary, key: string): string | undefined {
  let current: string | Dictionary | undefined = dict;
  for (const segment of key.split('.')) {
    if (typeof current !== 'object' || current === null) return undefined;
    current = current[segment];
  }
  return typeof current === 'string' ? current : undefined;
}

function interpolate(template: string, params?: TranslationParams): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => {
    const value = params[name];
    return value === undefined ? match : String(value);
  });
}

/**
 * Pure translation resolver. Looks ``key`` up as a dot path in ``dict``,
 * interpolates ``{var}`` placeholders from ``params``, and falls back — in
 * order — to ``fallback`` (the default ``es`` dictionary) and finally to the
 * raw key, so a missing string is always observable rather than blank.
 */
export function translate(
  dict: Dictionary,
  key: string,
  params?: TranslationParams,
  fallback?: Dictionary,
): string {
  const direct = resolvePath(dict, key);
  if (direct !== undefined) return interpolate(direct, params);

  if (fallback) {
    const fromFallback = resolvePath(fallback, key);
    if (fromFallback !== undefined) return interpolate(fromFallback, params);
  }

  return key;
}
