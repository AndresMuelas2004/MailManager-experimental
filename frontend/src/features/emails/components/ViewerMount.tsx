import EmailViewer from './EmailViewer';
import useAttachmentDownloader from '../hooks/useAttachmentDownloader';
import type { EmailMetadataOut, AccountOut } from '../../../api/types/dto';

type Props = {
  openedEmail: EmailMetadataOut | null;
  accounts: AccountOut[];
  onClose: () => void;
  onRead: (email: EmailMetadataOut) => Promise<void>;
  onReply: (email: EmailMetadataOut) => void | Promise<void>;
  onReplyAll: (email: EmailMetadataOut) => void | Promise<void>;
  onForward: (email: EmailMetadataOut) => void | Promise<void>;
};

export default function ViewerMount({
  openedEmail,
  accounts,
  onClose,
  onRead,
  onReply,
  onReplyAll,
  onForward,
}: Props) {
  if (!openedEmail) return null;
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

// Inner wrapper exists so the downloader hook is only instantiated when an
// email is actually open. Lifting the hook out of `EmailViewer` (and its
// inner `AttachmentsList`) satisfies features/CLAUDE.md §5.1 — components
// no longer call data-fetching hooks; the page passes them in.
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
  return (
    <EmailViewer
      mailboxId={openedEmail.mailbox_id}
      email={openedEmail}
      accounts={accounts}
      onClose={onClose}
      onRead={onRead}
      downloader={downloader}
      onReply={onReply}
      onReplyAll={onReplyAll}
      onForward={onForward}
    />
  );
}
