import { useCallback } from 'react';

import useEmailFolders from './useEmailFolders';
import useFolderList from './useFolderList';
import type { EmailMetadataOut, FolderOut } from '../../../api/types/dto';

type UseEmailFolderControlsReturn = {
  folders: FolderOut[];
  onAssignFolder: (email: EmailMetadataOut, folderId: string) => void;
  onUnassignFolder: (email: EmailMetadataOut, folderId: string) => void;
  isFolderBusy: (email: EmailMetadataOut) => boolean;
};

// Bundles the per-email folder assign wiring (catalogue + assign/unassign +
// pending flag) so each listing page drops it into EmailTable / ViewerMount
// with one hook call instead of repeating the plumbing. Every action routes by
// the row's OWN mailbox_id + account_id (a folder is unified and can surface an
// email whose account lives in another real mailbox — the same trap
// ``useFavorite`` handles). Assignment is best-effort: failures are swallowed
// here and reconciled by ``useEmailFolders``' onSettled invalidation.
export default function useEmailFolderControls(): UseEmailFolderControlsReturn {
  const emailFolders = useEmailFolders();
  const { folders } = useFolderList();

  const onAssignFolder = useCallback(
    (email: EmailMetadataOut, folderId: string) => {
      void emailFolders
        .assign({
          mailboxId: email.mailbox_id,
          accountId: email.account_id,
          providerMessageId: email.provider_message_id,
          folderId,
        })
        .catch(() => {});
    },
    [emailFolders],
  );

  const onUnassignFolder = useCallback(
    (email: EmailMetadataOut, folderId: string) => {
      void emailFolders
        .unassign({
          mailboxId: email.mailbox_id,
          accountId: email.account_id,
          providerMessageId: email.provider_message_id,
          folderId,
        })
        .catch(() => {});
    },
    [emailFolders],
  );

  const isFolderBusy = useCallback(
    (email: EmailMetadataOut) => emailFolders.isBusy(email.account_id, email.provider_message_id),
    [emailFolders],
  );

  return { folders, onAssignFolder, onUnassignFolder, isFolderBusy };
}
