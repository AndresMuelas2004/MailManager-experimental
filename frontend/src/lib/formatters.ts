import { getProviderMeta } from './providers';
import type { Lang } from './i18n/types';

const SECOND = 1_000;
const MINUTE = 60_000;
const HOUR = 3_600_000;
const DAY = 86_400_000;

const MONTHS_ES = [
  'ene',
  'feb',
  'mar',
  'abr',
  'may',
  'jun',
  'jul',
  'ago',
  'sep',
  'oct',
  'nov',
  'dic',
];

type AccountShape = {
  account_id: string;
  provider: string;
  email_address: string | null;
  display_label: string;
};

export function formatDate(dateStr: string): string {
  const d = new Date(dateStr);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
  }
  return `${d.getDate()} ${MONTHS_ES[d.getMonth()]}`;
}

export function formatShortDate(dateStr: string): string {
  const d = new Date(dateStr);
  return `${d.getDate()} ${MONTHS_ES[d.getMonth()]}`;
}

/**
 * Localized "time ago" for a past ``targetMs`` relative to ``nowMs`` using
 * Intl.RelativeTimeFormat (honours the interface ``lang`` es/en). ``nowMs`` is a
 * parameter — not Date.now() — so the function stays pure and unit-testable; the
 * caller supplies a ticking now. Future timestamps (clock skew, cross-tab
 * writes) clamp to 0 → "ahora" / "now". Unlike formatDate/formatShortDate
 * (Spanish-hardcoded, pre-i18n), this one is deliberately locale-aware.
 */
export function formatRelativeTime(targetMs: number, nowMs: number, lang: Lang): string {
  const diff = Math.max(0, nowMs - targetMs);
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: 'auto' });
  if (diff < MINUTE) return rtf.format(-Math.floor(diff / SECOND), 'second');
  if (diff < HOUR) return rtf.format(-Math.floor(diff / MINUTE), 'minute');
  if (diff < DAY) return rtf.format(-Math.floor(diff / HOUR), 'hour');
  return rtf.format(-Math.floor(diff / DAY), 'day');
}

// Normalises the "Re:" / "Fwd:" prefix stack to the base subject. Strips any
// leading run of those prefixes (case-insensitive, accepting the common locale
// variants); collapses to the empty-subject placeholder when the subject is
// empty or prefix-only. Used by every surface that renders a thread-level subject.
export function normaliseSubject(subject: string | null): string {
  const stripped = (subject ?? '').replace(/^(\s*(re|fwd|fw|rv)\s*:\s*)+/i, '').trim();
  return stripped.length > 0 ? stripped : '(Sin asunto)';
}

export function buildAccountMap<A extends AccountShape>(accounts: A[]): Map<string, A> {
  return new Map(accounts.map((a) => [a.account_id, a]));
}

export function resolveAccount(
  accountId: string,
  accountsById: Map<string, AccountShape>,
): { providerName: string; accountEmail: string } {
  const acc = accountsById.get(accountId);
  return {
    providerName: acc ? getProviderMeta(acc.provider).friendlyName : '',
    accountEmail: acc?.email_address ?? acc?.display_label ?? '',
  };
}
