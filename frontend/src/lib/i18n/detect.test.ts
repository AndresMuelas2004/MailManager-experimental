import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LANG_STORAGE_KEY, resolveInitialLang } from './detect';

function setNavigatorLanguage(value: string): void {
  vi.spyOn(navigator, 'language', 'get').mockReturnValue(value);
}

describe('resolveInitialLang', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it('returns the stored language when one is persisted', () => {
    window.localStorage.setItem(LANG_STORAGE_KEY, 'en');
    setNavigatorLanguage('es-ES');
    expect(resolveInitialLang()).toBe('en');
  });

  it('ignores an unrecognised stored value and falls back to detection', () => {
    window.localStorage.setItem(LANG_STORAGE_KEY, 'fr');
    setNavigatorLanguage('es-ES');
    expect(resolveInitialLang()).toBe('es');
  });

  it('detects English when the browser language starts with "en" and nothing is stored', () => {
    setNavigatorLanguage('en-US');
    expect(resolveInitialLang()).toBe('en');
  });

  it('defaults to Spanish for any non-English browser language', () => {
    setNavigatorLanguage('de-DE');
    expect(resolveInitialLang()).toBe('es');
  });
});
