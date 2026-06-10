import EmailViewer from './EmailViewer';
import ConversationViewerMount from './ConversationViewerMount';
import useAttachmentDownloader from '../hooks/useAttachmentDownloader';
import useEmailContent from '../hooks/useEmailContent';
import type { EmailMetadataOut, AccountOut } from '../../../api/types/dto';

type Props = {
  openedEmail: EmailMetadataOut | null;
  accounts: AccountOut[];
  onClose: () => void;
  onRead: (email: EmailMetadataOut) => Promise<void>;
  onReply: (email: EmailMetadataOut) => void | Promise<void>;
  onReplyAll: (email: EmailMetadataOut) => void | Promise<void>;
  onForward: (email: EmailMetadataOut) => void | Promise<void>;
  // When true (conversation-grouped boxes) the opened row represents a
  // thread, so mount the conversation viewer (full chain) instead of the
  // mono-message viewer. Favoritos leaves it false/absent.
  conversationMode?: boolean;
};

export default function ViewerMount({
  openedEmail,
  accounts,
  onClose,
  onRead,
  onReply,
  onReplyAll,
  onForward,
  conversationMode = false,
}: Props) {
  if (!openedEmail) return null;
  // Conversation path: the data-fetching hooks live per-message inside
  // ``ConversationMessageBody``, so neither ``useEmailContent`` nor the
  // downloader is instantiated at this level here. ``onRead`` is NOT
  // propagated — marking the whole thread read is orchestrated by the
  // conversation container.
  if (conversationMode) {
    return (
      <ConversationViewerMount
        openedEmail={openedEmail}
        onClose={onClose}
        onReply={onReply}
        onReplyAll={onReplyAll}
        onForward={onForward}
      />
    );
  }
  return (
    <ViewerWithDownloader
      openedEmail={openedEmail}
      accounts={accounts}
      onClose={onClose}
      onRead={onRead}
      onReply={onReply}
      onReplyAll={onReplyAll}
      onForward={onForward}
    />
  );
}

// Inner wrapper exists so the data-fetching hooks are only instantiated
// when an email is actually open. Lifting BOTH the downloader and the
// email-content hook out of `EmailViewer` (and its inner `AttachmentsList`)
// satisfies features/CLAUDE.md §5.1 — the viewer no longer calls any
// data-fetching hook; this wrapper passes the results in as props.
//
// ``mailboxId`` is derived from ``openedEmail.mailbox_id`` (the email
// carries its real mailbox in the listing payload). A virtual mailbox
// can aggregate emails whose ``account_id`` lives in a mailbox other
// than the one mounted at the route, so using the route's mailbox here
// would cause the content / attachment fetch to 404 with
// ``account_not_found``.
function ViewerWithDownloader({
  openedEmail,
  accounts,
  onClose,
  onRead,
  onReply,
  onReplyAll,
  onForward,
}: Omit<Props, 'openedEmail'> & { openedEmail: EmailMetadataOut }) {
  const downloader = useAttachmentDownloader({
    mailboxId: openedEmail.mailbox_id,
    accountId: openedEmail.account_id,
    providerMessageId: openedEmail.provider_message_id,
  });
  const { content, loading, error } = useEmailContent(openedEmail.mailbox_id, {
    account_id: openedEmail.account_id,
    provider_message_id: openedEmail.provider_message_id,
  });
  return (
    <EmailViewer
      email={openedEmail}
      accounts={accounts}
      content={content}
      loading={loading}
      error={error}
      onClose={onClose}
      onRead={onRead}
      downloader={downloader}
      onReply={onReply}
      onReplyAll={onReplyAll}
      onForward={onForward}
    />
  );
}
