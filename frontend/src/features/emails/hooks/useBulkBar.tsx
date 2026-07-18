import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import useSelection from '../../../lib/hooks/useSelection';
import { EMAILS_PAGE_SIZE } from '../../../lib/constants';
import useEmailBulkActions from './useEmailBulkActions';
import useEmailFolders from './useEmailFolders';
import BulkActionsBar from '../components/BulkActionsBar';
import type { BulkAction, ReadToggleTarget } from '../types';
import type { EmailBox } from '../../../lib/types';
import type { EmailMetadataOut, FolderRef } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

function emailKey(e: EmailMetadataOut): string {
  return `${e.account_id}|${e.provider_message_id}`;
}

type UseBulkBarArgs = {
  box: EmailBox;
  refresh: () => Promise<void>;
  // Debounced search term. Selection is cleared whenever it changes so a
  // selection made before searching does not survive into a filtered
  // result that no longer shows those rows.
  searchKey?: string;
  // Identifies the current listing scope (mailbox + account + box). The
  // router maps several boxes (and several accounts) onto the SAME page
  // component, changing only a prop / route param, so navigating between
  // them does NOT unmount the page. Without this the selection would
  // survive into the new box and stay actionable on the now-hidden emails
  // of the previous one. Cleared whenever it changes.
  scopeKey?: string;
  // Folder catalogue for the bulk "add to folder" picker (optional). When
  // present the bar shows the picker and a pick fans out one assign call per
  // selected email — there is NO bulk endpoint (backend MVP), so we route each
  // email by its own mailbox_id + account_id, exactly like the per-row menu.
  folders?: FolderRef[];
};

type UseBulkBarReturn = {
  selection: ReturnType<typeof useSelection<EmailMetadataOut>>;
  bulkError: UiError | null;
  bulkBar: ReactNode;
};

export default function useBulkBar({
  box,
  refresh,
  searchKey,
  scopeKey,
  folders,
}: UseBulkBarArgs): UseBulkBarReturn {
  const selection = useSelection<EmailMetadataOut>(emailKey);
  const emailFolders = useEmailFolders();

  // The Set inside ``useSelection`` only stores keys, so it forgets the
  // email objects of rows that leave the visible page. With pagination a
  // selection persists across pages, and the bulk actions need the full
  // ``EmailMetadataOut`` (``mailbox_id`` drives the per-mailbox fan-out)
  // and the read/unread counts of EVERY selected row, not just the
  // visible ones. This Map is the data store of the selection; the Set
  // remains the source of truth for "what is selected" (isSelected /
  // headerState / size, which the table consumes). The two are kept in
  // lockstep by the wrappers below. It must live in React state (not a
  // ref) so the bar re-renders when the selected data changes.
  const [selectedMap, setSelectedMap] = useState<Map<string, EmailMetadataOut>>(() => new Map());

  // ``selection.toggle`` / ``toggleTopN`` / ``clear`` keep a stable
  // identity (each is a useCallback inside useSelection), so depending on
  // the method alone keeps the wrappers stable. ``selection`` itself is a
  // fresh object literal every render, so we deliberately do NOT depend on
  // it (that would re-create the wrappers and churn the consumers).
  const wrappedToggle = useCallback(
    (email: EmailMetadataOut) => {
      selection.toggle(email);
      const key = emailKey(email);
      setSelectedMap((prev) => {
        const next = new Map(prev);
        if (next.has(key)) next.delete(key);
        else next.set(key, email);
        return next;
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selection.toggle],
  );

  const wrappedToggleTopN = useCallback(
    (pageEmails: EmailMetadataOut[]) => {
      selection.toggleTopN(pageEmails);
      setSelectedMap((prev) => {
        // Mirror useSelection.toggleTopN: if anything is selected, the
        // toggle clears everything; otherwise it selects the visible page
        // (capped at the page size).
        if (prev.size > 0) return new Map();
        const next = new Map<string, EmailMetadataOut>();
        for (const e of pageEmails.slice(0, EMAILS_PAGE_SIZE)) next.set(emailKey(e), e);
        return next;
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selection.toggleTopN],
  );

  const wrappedClear = useCallback(() => {
    selection.clear();
    setSelectedMap((prev) => (prev.size === 0 ? prev : new Map()));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection.clear]);

  // Clear the selection when the active search term changes (no unmount
  // happens on a same-page ``q`` change, so the Set/Map would otherwise
  // survive). Keyed by the debounced term, not the raw input, so it does
  // not fire on every intermediate keystroke.
  useEffect(() => {
    wrappedClear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchKey]);

  // Clear the selection when the view scope (mailbox / account / box)
  // changes. The router reuses the same page component across boxes and
  // accounts, so no unmount happens on those navigations and the Set/Map
  // would otherwise carry a phantom selection into the new view.
  useEffect(() => {
    wrappedClear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey]);

  // The wrapped selection is what every consumer (the 4 pages, the bulk
  // actions hook, the bar) must use: the pages call ``toggle`` /
  // ``toggleTopN`` / ``clear`` directly off this object, so the Map only
  // stays in sync if these point at the wrapped versions.
  const wrappedSelection = useMemo(
    () => ({
      ...selection,
      toggle: wrappedToggle,
      toggleTopN: wrappedToggleTopN,
      clear: wrappedClear,
    }),
    [selection, wrappedToggle, wrappedToggleTopN, wrappedClear],
  );

  const bulk = useEmailBulkActions({
    refresh,
    clearSelection: wrappedClear,
  });

  const { selected, readToggleTarget } = useMemo(() => {
    const sel = Array.from(selectedMap.values());
    let readCount = 0;
    for (const e of sel) if (e.is_read) readCount++;
    const unreadCount = sel.length - readCount;
    const target: ReadToggleTarget = unreadCount >= readCount ? 'mark_read' : 'mark_unread';
    return { selected: sel, readToggleTarget: target };
  }, [selectedMap]);

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
        case 'archive':
          return bulk.archiveItems(selected);
        case 'unarchive':
          return bulk.unarchiveItems(selected);
      }
    },
    [bulk, selected, readToggleTarget],
  );

  // Bulk "add to folder": one assign call per selected email (no bulk endpoint —
  // backend MVP), each routed by its own mailbox_id + account_id. The selection
  // is kept so the user can add the same selection to several folders in a row.
  const onAddToFolder = useCallback(
    (folderId: string) => {
      void Promise.all(
        selected.map((e) =>
          emailFolders.assign({
            mailboxId: e.mailbox_id,
            accountId: e.account_id,
            providerMessageId: e.provider_message_id,
            folderId,
          }),
        ),
      ).catch(() => undefined);
    },
    [selected, emailFolders],
  );

  const bulkBar = (
    <BulkActionsBar
      selectedCount={selected.length}
      box={box}
      readToggleTarget={readToggleTarget}
      disabled={bulk.loading || emailFolders.busy}
      onClear={wrappedClear}
      onAction={onAction}
      folders={folders}
      onAddToFolder={onAddToFolder}
    />
  );

  // The folder-add is best-effort: its failures are NOT folded into ``bulkError``
  // (which replaces the whole listing on the page) — the chips reconcile with
  // server truth via ``useEmailFolders``' ``onSettled`` invalidation instead. A
  // blanked listing on a transient assign failure would be worse UX than a chip
  // that simply does not stick.
  return { selection: wrappedSelection, bulkError: bulk.error, bulkBar };
}
