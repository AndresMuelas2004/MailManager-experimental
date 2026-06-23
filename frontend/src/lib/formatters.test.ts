import { describe, expect, it } from 'vitest';

import {
  buildAccountMap,
  formatDate,
  formatRelativeTime,
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

describe('formatRelativeTime', () => {
  // ``nowMs`` is fixed so the function is exercised deterministically (the real
  // ticking now is supplied by the caller — see RefreshControl). All Spanish
  // strings come from Intl.RelativeTimeFormat('es', { numeric: 'auto' }).
  const now = Date.parse('2024-06-01T12:00:00Z');

  it('says "ahora" for a zero delta (es)', () => {
    expect(formatRelativeTime(now, now, 'es')).toBe('ahora');
  });

  it('reports seconds for a sub-minute delta (es)', () => {
    expect(formatRelativeTime(now - 30_000, now, 'es')).toBe('hace 30 segundos');
  });

  it('reports minutes for a sub-hour delta (es)', () => {
    expect(formatRelativeTime(now - 5 * 60_000, now, 'es')).toBe('hace 5 minutos');
  });

  it('reports hours for a sub-day delta (es)', () => {
    expect(formatRelativeTime(now - 2 * 3_600_000, now, 'es')).toBe('hace 2 horas');
  });

  it('reports days for a ≥24h delta — "ayer" at exactly one day (es)', () => {
    // numeric: 'auto' renders -1 day as the idiomatic "ayer" rather than
    // "hace 1 día"; 25h floors to 1 day.
    expect(formatRelativeTime(now - 25 * 3_600_000, now, 'es')).toBe('ayer');
  });

  it('clamps a future target to zero → "ahora" (clock skew / cross-tab writes) (es)', () => {
    expect(formatRelativeTime(now + 60_000, now, 'es')).toBe('ahora');
  });

  it('honours the lang argument — English locale (en)', () => {
    expect(formatRelativeTime(now - 5 * 60_000, now, 'en')).toBe('5 minutes ago');
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
