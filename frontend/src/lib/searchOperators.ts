import type { EmailBox } from './types';

// Mirror of the backend's _IN_VALUES map (search-operators backend plan
// §3.2). Cosmetic only: it decides which "De/Para" columns the table
// shows when in: shifts the effective mailbox. It does NOT filter data —
// the backend parses q and applies the real box override. A divergence
// from the backend map degrades a column at worst, never the result set.
const IN_MAP: Record<string, EmailBox> = {
  inbox: 'ALL_MAIL',
  allmail: 'ALL_MAIL',
  sent: 'SENT',
  spam: 'SPAM',
  trash: 'TRASH',
  archive: 'ARCHIVE',
};

// Returns the box requested by the LAST valid in: in the string, or null.
// The (?:^|\s) anchor keeps in: from firing inside other tokens (e.g.
// "from:linkedin", "cousin:bob"); the optional ("?)…\1 group covers both
// in:sent and in:"sent". An unsupported value (in:archivados) is skipped,
// matching how the backend discards an invalid in:.
export function parseInOperator(q: string): EmailBox | null {
  let result: EmailBox | null = null;
  const re = /(?:^|\s)in:("?)([^\s"]+)\1/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(q)) !== null) {
    const mapped = IN_MAP[m[2].toLowerCase()];
    if (mapped) result = mapped; // last valid wins, matching the backend
  }
  return result;
}
