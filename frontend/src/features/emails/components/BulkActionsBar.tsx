import { useState } from 'react';
import {
  X,
  Trash2,
  Mail,
  MailOpen,
  ShieldAlert,
  ArchiveRestore,
  Archive,
  Inbox,
  Flame,
  FolderPlus,
} from 'lucide-react';

import { useTranslation } from '../../../lib/i18n';
import type { Translate } from '../../../lib/i18n';
import type { EmailBox } from '../../../lib/types';
import type { FolderRef } from '../../../api/types/dto';
import type { BulkAction, ReadToggleTarget } from '../types';
import { EMAIL_BOX_CONFIG } from '../boxes';

export type { BulkAction };

type Props = {
  selectedCount: number;
  box: EmailBox;
  readToggleTarget: ReadToggleTarget;
  disabled: boolean;
  onClear: () => void;
  onAction: (action: BulkAction) => void;
  // Bulk "add to folder" (optional). Orthogonal to ``box`` — folders never
  // move/archive the email — so it is NOT gated by ``EMAIL_BOX_CONFIG`` and
  // shows in every box when a catalogue + handler are supplied. Add-only:
  // ``onAddToFolder`` fans out one assign call per selected email in the hook.
  folders?: FolderRef[];
  onAddToFolder?: (folderId: string) => void;
};

function readLabel(t: Translate, target: ReadToggleTarget, count: number): string {
  const plural = count > 1;
  if (target === 'mark_read') {
    return plural ? t('bulk.markReadMany', { count }) : t('bulk.markReadOne', { count });
  }
  return plural ? t('bulk.markUnreadMany', { count }) : t('bulk.markUnreadOne', { count });
}

type ActionBtnProps = {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  onClick: () => void;
  disabled: boolean;
  danger?: boolean;
};

function ActionButton({ icon: Icon, label, onClick, disabled, danger }: ActionBtnProps) {
  const color = danger ? 'text-red-600 hover:bg-red-50' : 'text-zinc-700 hover:bg-zinc-100';
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-[13px] font-medium transition-colors disabled:opacity-50 ${color}`}
    >
      <Icon className="h-[18px] w-[18px]" />
      <span className="hidden lg:inline">{label}</span>
    </button>
  );
}

export default function BulkActionsBar({
  selectedCount,
  box,
  readToggleTarget,
  disabled,
  onClear,
  onAction,
  folders,
  onAddToFolder,
}: Props) {
  const { t } = useTranslation();
  const [folderPickerOpen, setFolderPickerOpen] = useState(false);
  const folderPickerEnabled = Boolean(folders && folders.length > 0 && onAddToFolder);
  const confirmDelete = () => {
    const msg =
      selectedCount > 1
        ? t('bulk.confirmDeleteMany', { count: selectedCount })
        : t('bulk.confirmDeleteOne');
    if (window.confirm(msg)) onAction('delete_permanently');
  };

  const readIcon = readToggleTarget === 'mark_read' ? MailOpen : Mail;
  const allowed = EMAIL_BOX_CONFIG[box].allowedBulkActions;
  const allows = (action: BulkAction) => allowed.includes(action);

  return (
    <div className="flex min-w-0 items-center gap-2 overflow-x-auto lg:overflow-visible">
      <button
        type="button"
        onClick={onClear}
        className="flex h-7 w-7 items-center justify-center rounded hover:bg-zinc-100"
        aria-label={t('bulk.clearSelection')}
      >
        <X className="h-[18px] w-[18px] text-zinc-600" />
      </button>
      <span className="text-[13px] font-medium text-zinc-700 lg:hidden">{selectedCount}</span>
      <span className="hidden text-[13px] font-medium text-zinc-700 lg:inline">
        {selectedCount === 1
          ? t('bulk.selectedOne', { count: selectedCount })
          : t('bulk.selectedMany', { count: selectedCount })}
      </span>
      <div className="mx-2 h-5 w-px bg-zinc-200" />

      {folderPickerEnabled && (
        <div className="relative">
          <ActionButton
            icon={FolderPlus}
            label={t('bulk.addToFolder')}
            onClick={() => setFolderPickerOpen((v) => !v)}
            disabled={disabled}
          />
          {folderPickerOpen && (
            <>
              <div
                className="fixed inset-0 z-40"
                aria-hidden
                onClick={() => setFolderPickerOpen(false)}
              />
              <div
                role="menu"
                className="absolute left-0 z-50 mt-1 max-h-64 w-56 overflow-auto rounded-lg border border-zinc-200 bg-white py-1 shadow-lg"
              >
                <p className="px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
                  {t('bulk.addToFolder')}
                </p>
                {folders!.map((folder) => (
                  <button
                    key={folder.folder_id}
                    type="button"
                    role="menuitem"
                    disabled={disabled}
                    onClick={() => {
                      onAddToFolder!(folder.folder_id);
                      setFolderPickerOpen(false);
                    }}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-zinc-50 disabled:opacity-50"
                  >
                    <span
                      className="h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: folder.color ?? '#a1a1aa' }}
                      aria-hidden
                    />
                    <span className="flex-1 truncate text-zinc-800">{folder.name}</span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {allows('toggle_read') && (
        <ActionButton
          icon={readIcon}
          label={readLabel(t, readToggleTarget, selectedCount)}
          onClick={() => onAction('toggle_read')}
          disabled={disabled}
        />
      )}

      {allows('archive') && (
        <ActionButton
          icon={Archive}
          label={t('bulk.archive')}
          onClick={() => onAction('archive')}
          disabled={disabled}
        />
      )}

      {allows('unarchive') && (
        <ActionButton
          icon={Inbox}
          label={t('bulk.unarchive')}
          onClick={() => onAction('unarchive')}
          disabled={disabled}
        />
      )}

      {allows('move_to_trash') && (
        <ActionButton
          icon={Trash2}
          label={t('bulk.moveToTrash')}
          onClick={() => onAction('move_to_trash')}
          disabled={disabled}
        />
      )}

      {allows('mark_spam') && (
        <ActionButton
          icon={ShieldAlert}
          label={t('bulk.markSpam')}
          onClick={() => onAction('mark_spam')}
          disabled={disabled}
        />
      )}

      {allows('restore_from_spam') && (
        <ActionButton
          icon={ArchiveRestore}
          label={t('bulk.restoreFromSpam')}
          onClick={() => onAction('restore_from_spam')}
          disabled={disabled}
        />
      )}

      {allows('restore_from_trash') && (
        <ActionButton
          icon={ArchiveRestore}
          label={t('bulk.restore')}
          onClick={() => onAction('restore_from_trash')}
          disabled={disabled}
        />
      )}

      {allows('delete_permanently') && (
        <ActionButton
          icon={Flame}
          label={t('bulk.deletePermanently')}
          onClick={confirmDelete}
          disabled={disabled}
          danger
        />
      )}
    </div>
  );
}
