import { useCallback, useState } from 'react';

import { updateReadStatus } from '../../../api/endpoints/emails';
import type { EmailMetadataOut, EmailItemRef } from '../../../api/types/dto';

function toItem(e: EmailMetadataOut): EmailItemRef {
  return { account_id: e.account_id, provider_message_id: e.provider_message_id };
}

export type UseEmailViewerReturn = {
  openedEmail: EmailMetadataOut | null;
  open: (email: EmailMetadataOut) => void;
  close: () => void;
  handleRead: (email: EmailMetadataOut) => Promise<void>;
};

export default function useEmailViewer(refresh: () => Promise<void>): UseEmailViewerReturn {
  const [openedEmail, setOpenedEmail] = useState<EmailMetadataOut | null>(null);

  const open = useCallback((email: EmailMetadataOut) => setOpenedEmail(email), []);
  const close = useCallback(() => setOpenedEmail(null), []);

  // Read the mailbox from the email itself (carried in the listing
  // payload). The viewer needs the email's real ``mailbox_id`` — which
  // can diverge from the route's ``mailboxId`` inside a virtual
  // mailbox whose scope spans multiple real mailboxes (``scope_kind``
  // ``'all'`` / ``'accounts'``). Using the route param here would
  // produce a 404 ``account_not_found`` whenever the account lives in
  // a different mailbox than the one mounted in the sidebar.
  const handleRead = useCallback(
    async (email: EmailMetadataOut) => {
      await updateReadStatus(email.mailbox_id, true, [toItem(email)]);
      await refresh();
    },
    [refresh],
  );

  return { openedEmail, open, close, handleRead };
}
