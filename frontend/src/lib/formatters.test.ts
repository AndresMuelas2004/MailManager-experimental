import { describe, expect, it } from 'vitest';

import {
  buildAccountMap,
  formatDate,
  formatShortDate,
  normaliseSubject,
  resolveAccount,
} from './formatters';

type TestAccount = {
  account_id: string;
  provider: string;
  email_address: string | null;
  display_label: string;
};

describe('formatDate', () => {
  it('returns HH:mm when the date falls on today', () => {
    const today = new Date();
    today.setHours(9, 30, 0, 0);
    const formatted = formatDate(today.toISOString());
    expect(formatted).toMatch(/^\d{2}:\d{2}$/);
  });

  it('returns "D mes" when the date is not today', () => {
    const pastIso = '2024-01-15T12:00:00Z';
    expect(formatDate(pastIso)).toMatch(/^\d{1,2} [a-z]{3}$/);
  });
});

describe('formatShortDate', () => {
  it('always returns "D mes"', () => {
    const today = new Date();
    expect(formatShortDate(today.toISOString())).toMatch(/^\d{1,2} [a-z]{3}$/);
    expect(formatShortDate('2020-06-20T10:00:00Z')).toMatch(/20 jun/);
  });
});

describe('normaliseSubject', () => {
  it('strips a single Re: prefix', () => {
    expect(normaliseSubject('Re: Hola')).toBe('Hola');
  });

  it('strips a stacked prefix run with locale variants, case-insensitively', () => {
    expect(normaliseSubject('RE: Fwd: FW: rv: Presupuesto')).toBe('Presupuesto');
  });

  it('keeps a subject without prefixes untouched', () => {
    expect(normaliseSubject('Presupuesto 2026')).toBe('Presupuesto 2026');
  });

  it('does not strip prefixes in the middle of the subject', () => {
    expect(normaliseSubject('Aviso: Re: no es prefijo')).toBe('Aviso: Re: no es prefijo');
  });

  it('collapses null, empty and prefix-only subjects to "(Sin asunto)"', () => {
    expect(normaliseSubject(null)).toBe('(Sin asunto)');
    expect(normaliseSubject('')).toBe('(Sin asunto)');
    expect(normaliseSubject('Re: Fwd:')).toBe('(Sin asunto)');
  });
});

describe('buildAccountMap', () => {
  it('indexes accounts by account_id', () => {
    const accounts: TestAccount[] = [
      { account_id: 'a1', provider: 'gmail', email_address: 'a@x', display_label: 'A' },
      { account_id: 'a2', provider: 'outlook', email_address: null, display_label: 'B' },
    ];
    const map = buildAccountMap(accounts);
    expect(map.size).toBe(2);
    expect(map.get('a1')?.provider).toBe('gmail');
    expect(map.get('a2')?.email_address).toBeNull();
  });
});

describe('resolveAccount', () => {
  const map = buildAccountMap<TestAccount>([
    { account_id: 'a1', provider: 'gmail', email_address: 'a@x', display_label: 'Mine' },
    { account_id: 'a2', provider: 'outlook', email_address: null, display_label: 'Work' },
  ]);

  it('returns the friendly provider name and the account email when present', () => {
    expect(resolveAccount('a1', map)).toEqual({
      providerName: 'Google',
      accountEmail: 'a@x',
    });
  });

  it('falls back to display_label when email_address is null', () => {
    expect(resolveAccount('a2', map)).toEqual({
      providerName: 'Microsoft',
      accountEmail: 'Work',
    });
  });

  it('returns empty strings for unknown account ids', () => {
    expect(resolveAccount('missing', map)).toEqual({
      providerName: '',
      accountEmail: '',
    });
  });
});
