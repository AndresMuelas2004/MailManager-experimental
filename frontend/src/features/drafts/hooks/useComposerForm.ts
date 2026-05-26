import { useCallback, useRef, useState } from 'react';

import type { DraftOut, ReplyKindDto } from '../../../api/types/dto';

export type ComposerSnapshot = {
  accountId: string;
  to: string;
  cc: string;
  bcc: string;
  subject: string;
  body: string;
};

export type ComposerFields = ComposerSnapshot;

export type DraftPayload = {
  to_recipients: string[];
  cc_recipients: string[];
  bcc_recipients: string[];
  subject: string;
  body: string;
};

// Reply / forward metadata kept in composer state. The values are
// persisted on the local ``drafts`` row at create time and read by
// the backend at send time. ``buildDraftPayload`` deliberately does
// NOT include them — the backend already has them in the row, and
// re-sending them from the frontend would be the wrong direction
// (the row is source of truth, see repository_guide.md invariant).
export type ReplyMetadata = {
  replyKind: ReplyKindDto | null;
  replyToMessageId: string | null;
  replyToAccountId: string | null;
  threadId: string | null;
  inReplyTo: string | null;
  referencesHeader: string | null;
};

const EMPTY_REPLY_METADATA: ReplyMetadata = {
  replyKind: null,
  replyToMessageId: null,
  replyToAccountId: null,
  threadId: null,
  inReplyTo: null,
  referencesHeader: null,
};

export type UseComposerFormReturn = {
  accountId: string;
  setAccountId: (id: string) => void;
  to: string;
  setTo: (v: string) => void;
  cc: string;
  setCc: (v: string) => void;
  bcc: string;
  setBcc: (v: string) => void;
  subject: string;
  setSubject: (v: string) => void;
  body: string;
  setBody: (v: string) => void;
  replyMetadata: ReplyMetadata;
  setReplyMetadata: (next: ReplyMetadata) => void;
  reset: () => void;
  seedFromDraft: (draft: DraftOut) => void;
  seedForReply: (args: {
    accountId: string;
    to: string[];
    cc: string[];
    subject: string;
    body: string;
    replyMetadata: ReplyMetadata;
  }) => void;
  getSnapshot: () => ComposerSnapshot;
  hasSavedSnapshot: () => boolean;
  isDirty: () => boolean;
  hasAnyContent: () => boolean;
  buildDraftPayload: () => DraftPayload;
  parseRecipients: (value: string) => string[];
};

function parseRecipientsImpl(value: string): string[] {
  return value
    .split(',')
    .map((r) => r.trim())
    .filter(Boolean);
}

function joinRecipients(items: string[]): string {
  return items.join(', ');
}

function snapshotsDiffer(a: ComposerSnapshot, b: ComposerSnapshot): boolean {
  return (
    a.to !== b.to ||
    a.cc !== b.cc ||
    a.bcc !== b.bcc ||
    a.subject !== b.subject ||
    a.body !== b.body
  );
}

export default function useComposerForm(): UseComposerFormReturn {
  const [accountId, setAccountId] = useState('');
  const [to, setTo] = useState('');
  const [cc, setCc] = useState('');
  const [bcc, setBcc] = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [replyMetadata, setReplyMetadata] = useState<ReplyMetadata>(EMPTY_REPLY_METADATA);
  const snapshotRef = useRef<ComposerSnapshot | null>(null);

  const reset = useCallback(() => {
    setAccountId('');
    setTo('');
    setCc('');
    setBcc('');
    setSubject('');
    setBody('');
    setReplyMetadata(EMPTY_REPLY_METADATA);
    snapshotRef.current = null;
  }, []);

  const seedFromDraft = useCallback((draft: DraftOut) => {
    const initialTo = joinRecipients(draft.to_recipients);
    const initialCc = joinRecipients(draft.cc_recipients);
    const initialBcc = joinRecipients(draft.bcc_recipients);
    setAccountId(draft.account_id);
    setTo(initialTo);
    setCc(initialCc);
    setBcc(initialBcc);
    setSubject(draft.subject);
    setBody(draft.body);
    // Propagate the reply metadata into composer state even though
    // it is not rendered today — see repository_guide.md invariant
    // about "reply fields persisted in row, not re-sent from frontend".
    // Without this, a future "convert draft to forward" feature would
    // silently lose the threading.
    setReplyMetadata({
      replyKind: draft.reply_kind ?? null,
      replyToMessageId: draft.reply_to_message_id ?? null,
      replyToAccountId: draft.reply_to_account_id ?? null,
      threadId: draft.thread_id ?? null,
      inReplyTo: draft.in_reply_to ?? null,
      referencesHeader: draft.references_header ?? null,
    });
    snapshotRef.current = {
      accountId: draft.account_id,
      to: initialTo,
      cc: initialCc,
      bcc: initialBcc,
      subject: draft.subject,
      body: draft.body,
    };
  }, []);

  const seedForReply = useCallback(
    (args: {
      accountId: string;
      to: string[];
      cc: string[];
      subject: string;
      body: string;
      replyMetadata: ReplyMetadata;
    }) => {
      const initialTo = joinRecipients(args.to);
      const initialCc = joinRecipients(args.cc);
      setAccountId(args.accountId);
      setTo(initialTo);
      setCc(initialCc);
      setBcc('');
      setSubject(args.subject);
      setBody(args.body);
      setReplyMetadata(args.replyMetadata);
      snapshotRef.current = {
        accountId: args.accountId,
        to: initialTo,
        cc: initialCc,
        bcc: '',
        subject: args.subject,
        body: args.body,
      };
    },
    [],
  );

  const getSnapshot = useCallback(
    (): ComposerSnapshot => ({ accountId, to, cc, bcc, subject, body }),
    [accountId, to, cc, bcc, subject, body],
  );

  const hasSavedSnapshot = useCallback(() => snapshotRef.current !== null, []);

  const isDirty = useCallback(() => {
    const snap = snapshotRef.current;
    if (!snap) return false;
    return snapshotsDiffer(getSnapshot(), snap);
  }, [getSnapshot]);

  const hasAnyContent = useCallback(
    () =>
      to.trim().length > 0 ||
      cc.trim().length > 0 ||
      bcc.trim().length > 0 ||
      subject.trim().length > 0 ||
      body.trim().length > 0,
    [to, cc, bcc, subject, body],
  );

  const buildDraftPayload = useCallback(
    (): DraftPayload => ({
      to_recipients: parseRecipientsImpl(to),
      cc_recipients: parseRecipientsImpl(cc),
      bcc_recipients: parseRecipientsImpl(bcc),
      subject,
      body,
    }),
    [to, cc, bcc, subject, body],
  );

  return {
    accountId,
    setAccountId,
    to,
    setTo,
    cc,
    setCc,
    bcc,
    setBcc,
    subject,
    setSubject,
    body,
    setBody,
    replyMetadata,
    setReplyMetadata,
    reset,
    seedFromDraft,
    seedForReply,
    getSnapshot,
    hasSavedSnapshot,
    isDirty,
    hasAnyContent,
    buildDraftPayload,
    parseRecipients: parseRecipientsImpl,
  };
}
