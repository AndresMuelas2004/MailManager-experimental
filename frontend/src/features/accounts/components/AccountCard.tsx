import { useState } from 'react';
import { Check, Loader2, Mail, Pencil, RefreshCw, Trash2, X } from 'lucide-react';

import { getProviderMeta, isGenericLabel } from '../../../lib/providers';
import { useTranslation } from '../../../lib/i18n';
import ConfirmModal from '../../../components/common/ConfirmModal';
import type { AccountOut } from '../../../api/types/dto';

type Props = {
  account: AccountOut;
  status: 'syncing' | 'ready' | 'error';
  // Background bulk-load progress (server state, resolved by the page from the
  // useBackfillStatus Map). Absent when the account has no active backfill job.
  backfillStatus?: 'pending' | 'running' | 'completed' | 'failed';
  backfillFetchedCount?: number;
  onEditLabel?: (label: string) => void;
  onReconnect?: () => void;
  onDelete?: () => void;
};

// Vertical management card, fixed height so two stack to the "Añadir cuenta"
// panel's height (the page grid places the panel one column wide, two rows
// tall, and lets these cards wrap around it). Identity (provider icon + name +
// email + live status) on top, the three inline icon actions on the bottom.
// Labels live in aria-label. No email preview, no click-through.
export default function AccountCard({
  account,
  status,
  backfillStatus,
  backfillFetchedCount,
  onEditLabel,
  onReconnect,
  onDelete,
}: Props) {
  const { t } = useTranslation();
  const meta = getProviderMeta(account.provider);

  // Show the live counter only while the backfill is in flight; completed /
  // failed / absent all collapse to the normal render (the label just retires
  // — a failed backfill shows no error notice by design, MVP).
  const isBackfilling = backfillStatus === 'pending' || backfillStatus === 'running';

  const hasCustomLabel = !isGenericLabel(account.display_label, account.provider);
  const email = account.email_address;
  // Name on top, email underneath — but only split them when there is a real
  // custom label; a generic provider label collapses to just the email.
  const primaryText = hasCustomLabel ? account.display_label : (email ?? account.display_label);
  const secondaryText = hasCustomLabel ? email : null;

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
    <div className="flex h-40 w-full flex-col justify-between rounded-lg border-[1.5px] border-zinc-200 bg-white p-3">
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
            className="h-8 w-full rounded-md border border-zinc-300 bg-white px-2 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-blue-600 focus:outline-none"
          />
          <div className="flex items-center justify-end gap-1">
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
          </div>
        </>
      ) : (
        <>
          {/* Identity block — name on top, email below, live status underneath. */}
          <div className="flex min-w-0 flex-col gap-0.5">
            <div className="flex items-center gap-1.5">
              <Mail className={`h-4 w-4 shrink-0 ${meta.headerTextClass}`} />
              <span
                className="min-w-0 flex-1 truncate text-sm font-semibold text-zinc-800"
                title={primaryText}
              >
                {primaryText}
              </span>
            </div>
            {secondaryText && (
              <span className="truncate pl-[22px] text-xs text-zinc-500" title={secondaryText}>
                {secondaryText}
              </span>
            )}
            {isBackfilling && (
              <span className="pl-[22px] text-[11px] font-medium text-blue-600">
                {t('accounts.backfillLoading', {
                  count: (backfillFetchedCount ?? 0).toLocaleString(),
                })}
              </span>
            )}
            {status === 'error' && (
              <span className="pl-[22px] text-[11px] text-red-500">{t('accounts.syncError')}</span>
            )}
          </div>

          {/* Action row — the three inline icon buttons, pinned to the bottom. */}
          <div className="flex items-center justify-end gap-1">
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
          </div>
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
