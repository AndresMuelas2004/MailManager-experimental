import { X, Trash2, Mail, MailOpen, ShieldAlert, ArchiveRestore, Flame } from 'lucide-react';

import { useTranslation } from '../../../lib/i18n';
import type { Translate } from '../../../lib/i18n';
import type { EmailBox } from '../../../lib/types';
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
      className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-[13px] font-medium transition-colors disabled:opacity-50 ${color}`}
    >
      <Icon className="h-[18px] w-[18px]" />
      {label}
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
}: Props) {
  const { t } = useTranslation();
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
    <div className="flex min-w-0 items-center gap-2">
      <button
        type="button"
        onClick={onClear}
        className="flex h-7 w-7 items-center justify-center rounded hover:bg-zinc-100"
        aria-label={t('bulk.clearSelection')}
      >
        <X className="h-[18px] w-[18px] text-zinc-600" />
      </button>
      <span className="text-[13px] font-medium text-zinc-700">
        {selectedCount === 1
          ? t('bulk.selectedOne', { count: selectedCount })
          : t('bulk.selectedMany', { count: selectedCount })}
      </span>
      <div className="mx-2 h-5 w-px bg-zinc-200" />

      {allows('toggle_read') && (
        <ActionButton
          icon={readIcon}
          label={readLabel(t, readToggleTarget, selectedCount)}
          onClick={() => onAction('toggle_read')}
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
