import { useState } from 'react';
import { Check, FolderPlus } from 'lucide-react';

import { useTranslation } from '../../lib/i18n';
import type { FolderRef } from '../../api/types/dto';

type Props = {
  folders: FolderRef[];
  // The folder_ids the email currently belongs to.
  assignedIds: Set<string>;
  onAssign: (folderId: string) => void;
  onUnassign: (folderId: string) => void;
  busy?: boolean;
  // Compact icon-only trigger for a listing row; the viewer passes false for a
  // labelled button.
  compact?: boolean;
};

// Presentational assign/unassign popover. Domain-aware but does no fetching:
// the folder catalogue, the assigned set and the callbacks all arrive by props
// (components/ui §2.2). Owns only its open/close UI state. Every click is
// stopped from propagating so opening the menu on a listing row never triggers
// the row's "open viewer" handler.
export default function FolderAssignMenu({
  folders,
  assignedIds,
  onAssign,
  onUnassign,
  busy = false,
  compact = false,
}: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  const toggle = (folder: FolderRef) => {
    if (assignedIds.has(folder.folder_id)) onUnassign(folder.folder_id);
    else onAssign(folder.folder_id);
  };

  return (
    <div className="relative" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={t('folderAssign.button')}
        aria-haspopup="menu"
        aria-expanded={open}
        className={
          compact
            ? 'grid h-8 w-8 place-items-center rounded-md text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700'
            : 'inline-flex items-center gap-1.5 rounded-[10px] border border-zinc-200 bg-white px-3 py-1.5 text-[13px] font-medium text-zinc-700 shadow-sm hover:bg-zinc-100'
        }
      >
        <FolderPlus className="h-4 w-4" />
        {compact ? null : t('folderAssign.button')}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" aria-hidden onClick={() => setOpen(false)} />
          <div
            role="menu"
            className="absolute right-0 z-50 mt-1 max-h-64 w-60 overflow-auto rounded-lg border border-zinc-200 bg-white py-1 shadow-lg"
          >
            <p className="px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
              {t('folderAssign.heading')}
            </p>
            {folders.length === 0 ? (
              <p className="px-3 py-2 text-xs text-zinc-500">{t('folderAssign.empty')}</p>
            ) : (
              folders.map((folder) => {
                const assigned = assignedIds.has(folder.folder_id);
                return (
                  <button
                    key={folder.folder_id}
                    type="button"
                    role="menuitemcheckbox"
                    aria-checked={assigned}
                    disabled={busy}
                    onClick={() => toggle(folder)}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-zinc-50 disabled:opacity-50"
                  >
                    <span
                      className="h-2.5 w-2.5 shrink-0 rounded-full"
                      // Folder colour is dynamic user data — inline style is the
                      // correct escape hatch. Neutral dot when no colour.
                      style={{ backgroundColor: folder.color ?? '#a1a1aa' }}
                      aria-hidden
                    />
                    <span className="flex-1 truncate text-zinc-800">{folder.name}</span>
                    {assigned && <Check className="h-4 w-4 shrink-0 text-blue-600" />}
                  </button>
                );
              })
            )}
          </div>
        </>
      )}
    </div>
  );
}
