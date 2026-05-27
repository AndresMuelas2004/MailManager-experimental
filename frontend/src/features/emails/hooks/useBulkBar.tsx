import { useCallback, useMemo } from 'react';
import type { ReactNode } from 'react';

import useSelection from '../../../lib/hooks/useSelection';
import useEmailBulkActions from './useEmailBulkActions';
import BulkActionsBar from '../components/BulkActionsBar';
import type { BulkAction, ReadToggleTarget } from '../types';
import type { EmailBox } from '../../../lib/types';
import type { EmailMetadataOut } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

function emailKey(e: EmailMetadataOut): string {
  return `${e.account_id}|${e.provider_message_id}`;
}

type UseBulkBarArgs = {
  box: EmailBox;
  emails: EmailMetadataOut[];
  refresh: () => Promise<void>;
};

type UseBulkBarReturn = {
  selection: ReturnType<typeof useSelection<EmailMetadataOut>>;
  bulkError: UiError | null;
  bulkBar: ReactNode;
};

export default function useBulkBar({ box, emails, refresh }: UseBulkBarArgs): UseBulkBarReturn {
  const selection = useSelection<EmailMetadataOut>(emailKey);
  const bulk = useEmailBulkActions({
    refresh,
    clearSelection: selection.clear,
  });

  const { selected, readToggleTarget } = useMemo(() => {
    const sel = selection.getSelected(emails);
    let readCount = 0;
    for (const e of sel) if (e.is_read) readCount++;
    const unreadCount = sel.length - readCount;
    const target: ReadToggleTarget = unreadCount >= readCount ? 'mark_read' : 'mark_unread';
    return { selected: sel, readToggleTarget: target };
  }, [emails, selection]);

  const onAction = useCallback(
    (action: BulkAction) => {
      switch (action) {
        case 'toggle_read':
          return bulk.setReadStatusItems(selected, readToggleTarget === 'mark_read');
        case 'move_to_trash':
          return bulk.moveToTrashItems(selected);
        case 'mark_spam':
          return bulk.spamItems(selected);
        case 'restore_from_spam':
          return bulk.restoreFromSpamItems(selected);
        case 'delete_permanently':
          return bulk.trashActionItems(selected, 'delete');
        case 'restore_from_trash':
          return bulk.trashActionItems(selected, 'restore');
      }
    },
    [bulk, selected, readToggleTarget],
  );

  const bulkBar = (
    <BulkActionsBar
      selectedCount={selected.length}
      box={box}
      readToggleTarget={readToggleTarget}
      disabled={bulk.loading}
      onClear={selection.clear}
      onAction={onAction}
    />
  );

  return { selection, bulkError: bulk.error, bulkBar };
}
