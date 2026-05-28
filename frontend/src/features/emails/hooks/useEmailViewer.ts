import { useCallback, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { updateReadStatus } from '../../../api/endpoints/emails';
import { toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';
import type { EmailMetadataOut, EmailItemRef } from '../../../api/types/dto';

function toItem(e: EmailMetadataOut): EmailItemRef {
  return { account_id: e.account_id, provider_message_id: e.provider_message_id };
}

export type UseEmailViewerReturn = {
  openedEmail: EmailMetadataOut | null;
  open: (email: EmailMetadataOut) => void;
  close: () => void;
  handleRead: (email: EmailMetadataOut) => Promise<void>;
  marking: boolean;
  error: UiError | null;
};

export default function useEmailViewer(): UseEmailViewerReturn {
  const [openedEmail, setOpenedEmail] = useState<EmailMetadataOut | null>(null);
  const queryClient = useQueryClient();

  const open = useCallback((email: EmailMetadataOut) => setOpenedEmail(email), []);
  const close = useCallback(() => setOpenedEmail(null), []);

  // Read the mailbox from the email itself (carried in the listing
  // payload). The viewer needs the email's real ``mailbox_id`` — which
  // can diverge from the route's ``mailboxId`` inside a virtual
  // mailbox whose accounts span multiple real mailboxes. Using the
  // route param here would produce a 404 ``account_not_found``
  // whenever the account lives in a different mailbox than the one
  // mounted in the sidebar.
  const readMutation = useMutation({
    mutationFn: (email: EmailMetadataOut) =>
      updateReadStatus(email.mailbox_id, true, [toItem(email)]),
    // Mark-as-read can surface on any listing (regular box or virtual
    // mailbox), so a blanket invalidation of both prefixes is the only
    // safe move — it mirrors useFavorite and keeps the read state
    // consistent across every mounted listing without threading a
    // per-page ``refresh`` callback through the hook.
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['emails'] });
      queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] });
    },
  });

  const handleRead = useCallback(
    async (email: EmailMetadataOut) => {
      await readMutation.mutateAsync(email);
    },
    [readMutation],
  );

  return {
    openedEmail,
    open,
    close,
    handleRead,
    marking: readMutation.isPending,
    error: readMutation.error ? toUiError(readMutation.error) : null,
  };
}
