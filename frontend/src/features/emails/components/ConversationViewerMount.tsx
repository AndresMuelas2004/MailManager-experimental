import { useEffect, useRef } from 'react';

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

// Container for the conversation viewer. Instantiates the data hook
// (``useConversation``) and orchestrates marking the whole thread read on
// open (decision 6) — both kept out of the presentational ``ConversationViewer``
// per features/CLAUDE.md §5.1. Identifies the conversation by the opened
// (representative) message's real mailbox/account/provider ids.
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

  // Mark the thread's unread messages read exactly once per open. The ref
  // guard mirrors ``readTriggered`` in ``EmailViewer``: once the chain has
  // loaded we fire a single grouped read-status call for the unread subset.
  // ``markRead`` no-ops on an empty array, so an all-read thread costs nothing.
  const readTriggered = useRef(false);
  useEffect(() => {
    if (readTriggered.current) return;
    if (loading) return;
    if (messages.length === 0) return;
    readTriggered.current = true;
    const unread = messages.filter((m) => !m.is_read);
    void markRead(unread);
  }, [loading, messages, markRead]);

  return (
    <ConversationViewer
      messages={messages}
      loading={loading}
      error={error}
      onClose={onClose}
      onReply={onReply}
      onReplyAll={onReplyAll}
      onForward={onForward}
    />
  );
}
