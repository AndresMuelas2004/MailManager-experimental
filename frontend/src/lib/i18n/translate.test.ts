import { describe, expect, it } from 'vitest';

import { translate, type Dictionary } from './translate';

const ACTIVE: Dictionary = {
  settings: {
    title: 'Settings',
    greeting: 'Hello {name}',
  },
};

const FALLBACK: Dictionary = {
  settings: {
    title: 'Ajustes',
    onlyInFallback: 'Solo en el fallback',
  },
};

describe('translate', () => {
  it('resolves a value by dotted path', () => {
    expect(translate(ACTIVE, 'settings.title')).toBe('Settings');
  });

  it('interpolates {var} placeholders from params', () => {
    expect(translate(ACTIVE, 'settings.greeting', { name: 'Ada' })).toBe('Hello Ada');
  });

  it('leaves an unmatched placeholder untouched when no param is supplied', () => {
    expect(translate(ACTIVE, 'settings.greeting')).toBe('Hello {name}');
  });

  it('falls back to the fallback dictionary when the key is missing in the active one', () => {
    expect(translate(ACTIVE, 'settings.onlyInFallback', undefined, FALLBACK)).toBe(
      'Solo en el fallback',
    );
  });

  it('falls back to the raw key when it is missing in both dictionaries', () => {
    expect(translate(ACTIVE, 'settings.missing.deep', undefined, FALLBACK)).toBe(
      'settings.missing.deep',
    );
  });

  it('returns the raw key when a path resolves to a non-string node', () => {
    // 'settings' is an object, not a leaf string — must not be returned as-is.
    expect(translate(ACTIVE, 'settings')).toBe('settings');
  });
});
