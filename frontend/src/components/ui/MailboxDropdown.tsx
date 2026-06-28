import { useState } from 'react';
import { Inbox, Plus, Check, Pencil, Trash2, X } from 'lucide-react';

import { useTranslation } from '../../lib/i18n';

type MailboxItem = {
  mailbox_id: string;
  display_name: string | null;
};

type Props = {
  mailboxes: MailboxItem[];
  currentMailboxId: string;
  onSelect: (mailboxId: string) => void;
  onCreate: (displayName: string) => void;
  onRename: (mailboxId: string, displayName: string) => void;
  onRequestDelete: (mailbox: MailboxItem) => void;
};

export default function MailboxDropdown({
  mailboxes,
  currentMailboxId,
  onSelect,
  onCreate,
  onRename,
  onRequestDelete,
}: Props) {
  const { t } = useTranslation();
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  // Inline rename is only ever offered for the active mailbox (the second entry
  // point documented in buzones-y-vista-unificada.md §2.4), so a single boolean
  // suffices — no per-row id is needed.
  const [renaming, setRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState('');

  function handleCreate() {
    const trimmed = newName.trim();
    if (trimmed.length === 0) return;
    onCreate(trimmed);
    setNewName('');
    setCreating(false);
  }

  function startRename(current: string | null) {
    setRenameDraft(current ?? '');
    setRenaming(true);
  }

  function cancelRename() {
    setRenaming(false);
    setRenameDraft('');
  }

  function submitRename() {
    const trimmed = renameDraft.trim();
    if (trimmed.length === 0) return;
    onRename(currentMailboxId, trimmed);
    setRenaming(false);
    setRenameDraft('');
  }

  return (
    <div className="absolute left-0 top-full z-20 mt-1 w-full rounded-xl border border-zinc-200 bg-white py-1 shadow-lg">
      {mailboxes.map((m) => {
        const isCurrent = m.mailbox_id === currentMailboxId;

        if (isCurrent && renaming) {
          return (
            <div key={m.mailbox_id} className="flex items-center gap-2 px-3 py-2">
              <input
                type="text"
                value={renameDraft}
                onChange={(e) => setRenameDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') submitRename();
                  if (e.key === 'Escape') cancelRename();
                }}
                placeholder={t('mailboxesSettings.renamePlaceholder')}
                maxLength={120}
                autoFocus
                className="h-8 min-w-0 flex-1 rounded-lg border border-zinc-200 px-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-blue-600 focus:outline-none"
              />
              <button
                type="button"
                onClick={submitRename}
                disabled={renameDraft.trim().length === 0}
                aria-label={t('common.save')}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-600 text-white disabled:opacity-40"
              >
                <Check className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={cancelRename}
                aria-label={t('common.cancel')}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-zinc-200 text-zinc-500 hover:bg-zinc-50"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          );
        }

        return (
          <div
            key={m.mailbox_id}
            className="flex w-full items-center gap-1 pr-2 text-sm hover:bg-zinc-50"
          >
            <button
              type="button"
              onClick={() => onSelect(m.mailbox_id)}
              className="flex flex-1 items-center gap-2.5 px-3 py-2.5 text-left"
            >
              <Inbox className="h-4 w-4 text-blue-600" />
              <span className="flex-1 text-zinc-900">{m.display_name}</span>
              {isCurrent && <Check className="h-4 w-4 text-blue-600" />}
            </button>
            {isCurrent && (
              <>
                <button
                  type="button"
                  onClick={() => startRename(m.display_name)}
                  aria-label={t('mailboxesSettings.renameAria')}
                  className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-zinc-500 hover:bg-zinc-100 hover:text-zinc-700"
                >
                  <Pencil className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={() => onRequestDelete(m)}
                  aria-label={t('mailboxesSettings.deleteAria')}
                  className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-red-500 hover:bg-red-50"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </>
            )}
          </div>
        );
      })}

      <div className="mx-2 my-1 h-px bg-zinc-100" />

      {creating ? (
        <div className="flex items-center gap-2 px-3 py-2">
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleCreate();
            }}
            placeholder={t('mailboxesSettings.renamePlaceholder')}
            maxLength={120}
            autoFocus
            className="h-8 flex-1 rounded-lg border border-zinc-200 px-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-blue-600 focus:outline-none"
          />
          <button
            type="button"
            onClick={handleCreate}
            disabled={newName.trim().length === 0}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-600 text-white disabled:opacity-40"
          >
            <Check className="h-4 w-4" />
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm text-blue-600 hover:bg-zinc-50"
        >
          <Plus className="h-4 w-4" />
          {t('createMailbox.dropdownCreate')}
        </button>
      )}
    </div>
  );
}
