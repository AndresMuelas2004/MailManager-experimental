import { getProviderMeta } from './providers';

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

// Normalises the "Re:" / "Fwd:" prefix stack to the base subject. Strips any
// leading run of those prefixes (case-insensitive, accepting the common locale
// variants); collapses to "(Sin asunto)" when the subject is empty or
// prefix-only. Used by every surface that renders a thread-level subject.
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
