import { useEffect, useMemo, useRef } from 'react';

import ConversationViewer from './ConversationViewer';
import useConversation from '../hooks/useConversation';
import useMarkThreadRead from '../hooks/useMarkThreadRead';
import type { EmailMetadataOut } from '../../../api/types/dto';

type Props = {
  openedEmail: EmailMetadataOut;
  onClose: () => void;
  onReply: (email: EmailMetadataOut) => void | Promise<void>;
  onReplyAll: (email: EmailMetadataOut) => void | Promise<void>;
  onForward: (email: EmailMetadataOut) => void | Promise<void>;
};

function messageKey(message: EmailMetadataOut): string {
  return `${message.account_id}|${message.provider_message_id}`;
}

// True when ``m`` is the same physical message the user opened (``openedEmail``,
// the listing row). Gmail and threads without an id-mismatch match by
// ``provider_message_id``; Outlook returns a DIFFERENT REST id for the same
// message on the ``fetch_conversation`` endpoint than on the sync/listing one,
// so the same physical message is matched by identical (received_at, from_email)
// there. This is what dedups the opened email against its conversation twin so
// it is not shown twice.
function isSameAsOpened(m: EmailMetadataOut, openedEmail: EmailMetadataOut): boolean {
  if (m.account_id !== openedEmail.account_id) return false;
  return (
    m.provider_message_id === openedEmail.provider_message_id ||
    (m.received_at === openedEmail.received_at && m.from_email === openedEmail.from_email)
  );
}

// Chronological ascending order (oldest first), stable tie-break by
// provider_message_id so the merged list is deterministic when two messages
// share a timestamp (Outlook twins).
function compareByReceivedAtAsc(a: EmailMetadataOut, b: EmailMetadataOut): number {
  const ta = new Date(a.received_at).getTime();
  const tb = new Date(b.received_at).getTime();
  if (ta !== tb) return ta - tb;
  return a.provider_message_id.localeCompare(b.provider_message_id);
}

// Container for the conversation viewer. Instantiates the data hook
// (``useConversation``) and orchestrates marking the whole thread read on
// open (decision 6) — both kept out of the presentational ``ConversationViewer``
// per features/CLAUDE.md §5.1. Identifies the conversation by the opened
// (representative) message's real mailbox/account/provider ids.
//
// The opened email's body is painted IMMEDIATELY from the warmed cache instead
// of waiting for ``/conversation`` (a live provider call, never prefetched):
// ``displayMessages`` merges ``openedEmail`` (whose provider_message_id is the
// key the prefetch warmed → cache hit on BOTH providers) with the chain, and
// the chain fills in the rest below as it arrives.
export default function ConversationViewerMount({
  openedEmail,
  onClose,
  onReply,
  onReplyAll,
  onForward,
}: Props) {
  const { messages, loading, error } = useConversation(
    openedEmail.mailbox_id,
    openedEmail.account_id,
    openedEmail.provider_message_id,
    true,
  );
  const { markRead } = useMarkThreadRead();

  // The messages to render: ``openedEmail`` (listing id → cache hit) merged with
  // the chain, deduping the opened message's twin so it never appears twice, in
  // chronological order. While the chain is still loading this is just
  // ``[openedEmail]`` → the opened email shows instantly, expanded, with no
  // full-screen conversation spinner.
  const displayMessages = useMemo(() => {
    const rest = messages.filter((m) => !isSameAsOpened(m, openedEmail));
    return [openedEmail, ...rest].sort(compareByReceivedAtAsc);
  }, [openedEmail, messages]);

  // Mark the thread's unread messages read exactly once per open. The ref
  // guard mirrors ``readTriggered`` in ``EmailViewer``: once the chain has
  // loaded we fire a single grouped read-status call for the unread subset.
  // ``markRead`` no-ops on an empty array, so an all-read thread costs nothing.
  //
  // ``openedEmail`` (the listing row) is included explicitly and FIRST, then
  // the conversation members. Outlook assigns different REST ids to the same
  // message depending on the endpoint: the sync/listing stores the folder-delta
  // id, while ``fetch_conversation`` (``/messages?$filter=conversationId``)
  // returns a different id. The DB rows are keyed by the listing id, so marking
  // the conversation members alone updates the wrong row and the listing never
  // flips to read on Outlook. Marking ``openedEmail`` by its listing id fixes
  // that; the members still cover the rest of the thread on Gmail, where ids
  // are consistent. Dedup by (account_id, provider_message_id) so Gmail does
  // not mark the representative twice.
  const readTriggered = useRef(false);
  useEffect(() => {
    if (readTriggered.current) return;
    if (loading) return;
    if (messages.length === 0) return;
    readTriggered.current = true;
    const seen = new Set<string>();
    const unread = [openedEmail, ...messages].filter((m) => {
      if (m.is_read) return false;
      const key = `${m.account_id}|${m.provider_message_id}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    void markRead(unread);
  }, [loading, messages, markRead, openedEmail]);

  return (
    <ConversationViewer
      messages={displayMessages}
      defaultExpandedKey={messageKey(openedEmail)}
      threadLoading={loading}
      error={error}
      onClose={onClose}
      onReply={onReply}
      onReplyAll={onReplyAll}
      onForward={onForward}
    />
  );
}
