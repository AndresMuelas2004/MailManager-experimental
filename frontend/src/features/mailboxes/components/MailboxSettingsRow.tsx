import { useState } from 'react';
import { Check, Inbox, Pencil, Trash2, X } from 'lucide-react';

import { useTranslation } from '../../../lib/i18n';

type Props = {
  displayName: string | null;
  busy: boolean;
  onRename: (displayName: string) => void;
  onRequestDelete: () => void;
};

// Presentational row for one mailbox in the settings list. Holds only local UI
// state (whether the inline rename input is open and its draft value); the
// rename/delete side effects are emitted through callbacks. Navigation and
// HTTP live in the page/hooks (components/CLAUDE.md §3.5).
export default function MailboxSettingsRow({
  displayName,
  busy,
  onRename,
  onRequestDelete,
}: Props) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  const startEditing = () => {
    setDraft(displayName ?? '');
    setEditing(true);
  };

  const cancelEditing = () => {
    setEditing(false);
    setDraft('');
  };

  const submit = () => {
    const trimmed = draft.trim();
    if (trimmed.length === 0) return;
    onRename(trimmed);
    setEditing(false);
    setDraft('');
  };

  return (
    <li className="flex items-center gap-3 border-b border-zinc-100 px-4 py-4 lg:px-8">
      <Inbox className="h-5 w-5 shrink-0 text-zinc-400" />

      {editing ? (
        <div className="flex flex-1 items-center gap-2">
          <input
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit();
              if (e.key === 'Escape') cancelEditing();
            }}
            placeholder={t('mailboxesSettings.renamePlaceholder')}
            maxLength={120}
            autoFocus
            className="h-9 flex-1 rounded-lg border-[1.5px] border-zinc-200 px-3 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-blue-600 focus:outline-none"
          />
          <button
            type="button"
            onClick={submit}
            disabled={busy || draft.trim().length === 0}
            aria-label={t('common.save')}
            className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-blue-600 text-white disabled:opacity-40"
          >
            <Check className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={cancelEditing}
            aria-label={t('common.cancel')}
            className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-zinc-200 text-zinc-500 hover:bg-zinc-50"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      ) : (
        <>
          <span className="flex-1 truncate text-sm font-medium text-zinc-900">
            {displayName ?? t('mailboxesSettings.untitled')}
          </span>
          <button
            type="button"
            onClick={startEditing}
            disabled={busy}
            aria-label={t('mailboxesSettings.renameAria')}
            className="grid h-8 w-8 place-items-center rounded-md text-zinc-500 hover:bg-zinc-100 hover:text-zinc-700 disabled:opacity-50"
          >
            <Pencil className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={onRequestDelete}
            disabled={busy}
            aria-label={t('mailboxesSettings.deleteAria')}
            className="grid h-8 w-8 place-items-center rounded-md text-red-500 hover:bg-red-50 disabled:opacity-50"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </>
      )}
    </li>
  );
}
