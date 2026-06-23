import { useEffect, useRef, useState } from 'react';
import { Check, EllipsisVertical, Loader2, Mail, X } from 'lucide-react';

import { formatShortDate } from '../../../lib/formatters';
import { getProviderMeta, isGenericLabel } from '../../../lib/providers';
import { useTranslation } from '../../../lib/i18n';
import Badge from '../../../components/common/Badge';
import type { AccountOut, EmailMetadataOut } from '../../../api/types/dto';
import AccountCardDropdown from './AccountCardDropdown';

type Props = {
  account: AccountOut;
  emails: EmailMetadataOut[];
  status: 'syncing' | 'ready' | 'error';
  unreadCount?: number;
  onClick?: () => void;
  onEditLabel?: (label: string) => void;
  onReconnect?: () => void;
  onDelete?: () => void;
};

export default function AccountCard({
  account,
  emails,
  status,
  unreadCount,
  onClick,
  onEditLabel,
  onReconnect,
  onDelete,
}: Props) {
  const { t } = useTranslation();
  const meta = getProviderMeta(account.provider);
  const headerBg = meta.headerBgClass;
  const headerColor = meta.headerTextClass;

  const hasCustomLabel = !isGenericLabel(account.display_label, account.provider);
  const email = account.email_address;
  const headerText =
    hasCustomLabel && email
      ? `${account.display_label} - ${email}`
      : (email ?? account.display_label);

  const hasActions = !!onDelete || !!onReconnect || !!onEditLabel;
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;

    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [menuOpen]);

  const startEditing = () => {
    setMenuOpen(false);
    setDraft(account.display_label);
    setEditing(true);
  };

  const submitEditing = () => {
    const trimmed = draft.trim();
    if (trimmed.length === 0) return;
    onEditLabel?.(trimmed);
    setEditing(false);
  };

  return (
    <div
      role={onClick && !editing ? 'button' : undefined}
      tabIndex={onClick && !editing ? 0 : undefined}
      onClick={editing ? undefined : onClick}
      onKeyDown={
        onClick && !editing
          ? (e) => {
              if (e.key === 'Enter') onClick();
            }
          : undefined
      }
      className={`flex w-full flex-col overflow-hidden rounded-2xl border-[1.5px] border-zinc-200 bg-white ${
        onClick && !editing ? 'cursor-pointer transition-shadow hover:shadow-md' : ''
      }`}
    >
      <div className={`flex items-center gap-2 px-4 py-3 ${headerBg}`}>
        {editing ? (
          <div
            className="flex min-w-0 flex-1 items-center gap-2"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
            role="presentation"
          >
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
              className="grid h-6 w-6 shrink-0 place-items-center rounded bg-blue-600 text-white disabled:opacity-40"
            >
              <Check className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              aria-label={t('common.cancel')}
              className="grid h-6 w-6 shrink-0 place-items-center rounded text-zinc-500 hover:bg-black/5"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : (
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <Mail className={`h-3.5 w-3.5 shrink-0 ${headerColor}`} />
            <span className={`truncate text-xs font-semibold ${headerColor}`}>{headerText}</span>
          </div>
        )}
        {!editing && (
          <Badge
            count={unreadCount ?? 0}
            aria-label={t('nav.unreadBadge', { count: unreadCount ?? 0 })}
          />
        )}
        {hasActions && !editing && (
          <div
            ref={menuRef}
            className="relative"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
            role="presentation"
          >
            <button
              type="button"
              onClick={() => setMenuOpen((prev) => !prev)}
              className={`rounded p-1 hover:bg-black/5 ${headerColor}`}
            >
              <EllipsisVertical className="h-3.5 w-3.5" />
            </button>
            {menuOpen && (
              <AccountCardDropdown
                onEditLabel={onEditLabel ? startEditing : undefined}
                onReconnect={
                  onReconnect
                    ? () => {
                        setMenuOpen(false);
                        onReconnect();
                      }
                    : undefined
                }
                onDelete={
                  onDelete
                    ? () => {
                        setMenuOpen(false);
                        onDelete();
                      }
                    : undefined
                }
              />
            )}
          </div>
        )}
      </div>

      {status === 'syncing' && (
        <div className="flex flex-col items-center justify-center gap-3 px-4 py-10 text-center">
          <Loader2 className="h-6 w-6 animate-spin text-zinc-400" />
          <p className="text-xs text-zinc-400">{t('accounts.syncingEmails')}</p>
        </div>
      )}

      {status === 'error' && (
        <div className="px-4 py-8 text-center">
          <p className="text-xs text-red-500">{t('accounts.syncError')}</p>
        </div>
      )}

      {status === 'ready' && emails.length === 0 && (
        <div className="px-4 py-8 text-center">
          <p className="text-xs text-zinc-400">{t('accounts.noEmailsYet')}</p>
        </div>
      )}

      {status === 'ready' && emails.length > 0 && (
        <div className="flex flex-col">
          {emails.map((emailItem, i) => (
            <div
              key={emailItem.provider_message_id}
              className={`flex flex-col gap-0.5 px-3.5 py-2.5 ${
                i > 0 ? 'border-t border-zinc-100' : ''
              } ${emailItem.is_read ? 'bg-white' : 'bg-zinc-50'}`}
            >
              <div className="flex items-center justify-between">
                <span
                  className={`text-xs ${
                    emailItem.is_read ? 'font-normal' : 'font-semibold'
                  } text-zinc-900`}
                >
                  {emailItem.from_name ?? emailItem.from_email}
                </span>
                <span className="text-[10px] text-zinc-400">
                  {formatShortDate(emailItem.received_at)}
                </span>
              </div>
              <span
                className={`text-[11px] ${
                  emailItem.is_read ? 'font-normal text-zinc-500' : 'font-medium text-zinc-600'
                }`}
              >
                {emailItem.subject ?? t('common.noSubject')}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
