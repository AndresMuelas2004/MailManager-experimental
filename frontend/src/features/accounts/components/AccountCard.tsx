import { useState } from 'react';
import { Check, Loader2, Mail, Pencil, RefreshCw, Trash2, X } from 'lucide-react';

import { getProviderMeta, isGenericLabel } from '../../../lib/providers';
import { useTranslation } from '../../../lib/i18n';
import ConfirmModal from '../../../components/common/ConfirmModal';
import type { AccountOut } from '../../../api/types/dto';

type Props = {
  account: AccountOut;
  status: 'syncing' | 'ready' | 'error';
  onEditLabel?: (label: string) => void;
  onReconnect?: () => void;
  onDelete?: () => void;
};

// Thin, single-row management card: provider-coloured icon + label/email + the
// three inline actions collapsed to icon buttons (labels live in aria-label so
// the row stays one line high). No email preview, no click-through.
export default function AccountCard({
  account,
  status,
  onEditLabel,
  onReconnect,
  onDelete,
}: Props) {
  const { t } = useTranslation();
  const meta = getProviderMeta(account.provider);

  const hasCustomLabel = !isGenericLabel(account.display_label, account.provider);
  const email = account.email_address;
  const headerText =
    hasCustomLabel && email
      ? `${account.display_label} - ${email}`
      : (email ?? account.display_label);

  const [confirmDelete, setConfirmDelete] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const reconnecting = status === 'syncing';

  const startEditing = () => {
    setDraft(account.display_label);
    setEditing(true);
  };

  const submitEditing = () => {
    const trimmed = draft.trim();
    if (trimmed.length === 0) return;
    onEditLabel?.(trimmed);
    setEditing(false);
  };

  const iconBtn =
    'grid h-7 w-7 shrink-0 place-items-center rounded-md transition-colors disabled:opacity-40';

  return (
    <div className="flex h-11 items-center gap-1.5 rounded-lg border-[1.5px] border-zinc-200 bg-white px-2.5">
      {editing ? (
        <>
          <input
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submitEditing();
              if (e.key === 'Escape') setEditing(false);
            }}
            placeholder={t('accounts.editLabelPlaceholder')}
            maxLength={120}
            autoFocus
            className="h-7 min-w-0 flex-1 rounded-md border border-zinc-300 bg-white px-2 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-blue-600 focus:outline-none"
          />
          <button
            type="button"
            onClick={submitEditing}
            disabled={draft.trim().length === 0}
            aria-label={t('common.save')}
            className={`${iconBtn} bg-blue-600 text-white disabled:opacity-40`}
          >
            <Check className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setEditing(false)}
            aria-label={t('common.cancel')}
            className={`${iconBtn} text-zinc-500 hover:bg-zinc-100`}
          >
            <X className="h-4 w-4" />
          </button>
        </>
      ) : (
        <>
          <Mail className={`h-4 w-4 shrink-0 ${meta.headerTextClass}`} />
          <span
            className="min-w-0 flex-1 truncate text-xs font-medium text-zinc-800"
            title={headerText}
          >
            {headerText}
          </span>
          {status === 'error' && (
            <span className="shrink-0 text-[10px] text-red-500">{t('accounts.syncError')}</span>
          )}
          {onEditLabel && (
            <button
              type="button"
              onClick={startEditing}
              disabled={reconnecting}
              aria-label={t('accounts.editLabel')}
              className={`${iconBtn} text-zinc-500 hover:bg-zinc-100`}
            >
              <Pencil className="h-3.5 w-3.5" />
            </button>
          )}
          {onReconnect && (
            <button
              type="button"
              onClick={onReconnect}
              disabled={reconnecting}
              aria-label={t('accounts.reconnect')}
              className={`${iconBtn} text-zinc-500 hover:bg-zinc-100`}
            >
              {reconnecting ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
            </button>
          )}
          {onDelete && (
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              disabled={reconnecting}
              aria-label={t('accounts.delete')}
              className={`${iconBtn} text-red-500 hover:bg-red-50`}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          )}
        </>
      )}

      {confirmDelete && onDelete && (
        <ConfirmModal
          title={t('accounts.confirmDeleteTitle')}
          description={t('accounts.confirmDeleteDescription')}
          confirmLabel={t('common.delete')}
          cancelLabel={t('common.cancel')}
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() => {
            setConfirmDelete(false);
            onDelete();
          }}
        />
      )}
    </div>
  );
}
