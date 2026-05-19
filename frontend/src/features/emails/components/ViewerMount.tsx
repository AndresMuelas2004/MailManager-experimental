import EmailViewer from './EmailViewer';
import useAttachmentDownloader from '../hooks/useAttachmentDownloader';
import type { EmailMetadataOut, AccountOut } from '../../../api/types/dto';

type Props = {
  mailboxId: string;
  openedEmail: EmailMetadataOut | null;
  accounts: AccountOut[];
  onClose: () => void;
  onRead: (email: EmailMetadataOut) => Promise<void>;
};

export default function ViewerMount({ mailboxId, openedEmail, accounts, onClose, onRead }: Props) {
  if (!openedEmail) return null;
  return (
    <ViewerWithDownloader
      mailboxId={mailboxId}
      openedEmail={openedEmail}
      accounts={accounts}
      onClose={onClose}
      onRead={onRead}
    />
  );
}

// Inner wrapper exists so the downloader hook is only instantiated when an
// email is actually open. Lifting the hook out of `EmailViewer` (and its
// inner `AttachmentsList`) satisfies features/CLAUDE.md §5.1 — components
// no longer call data-fetching hooks; the page passes them in.
function ViewerWithDownloader({
  mailboxId,
  openedEmail,
  accounts,
  onClose,
  onRead,
}: Omit<Props, 'openedEmail'> & { openedEmail: EmailMetadataOut }) {
  const downloader = useAttachmentDownloader({
    mailboxId,
    accountId: openedEmail.account_id,
    providerMessageId: openedEmail.provider_message_id,
  });
  return (
    <EmailViewer
      mailboxId={mailboxId}
      email={openedEmail}
      accounts={accounts}
      onClose={onClose}
      onRead={onRead}
      downloader={downloader}
    />
  );
}
