import { useMemo } from 'react';
import type { ReactNode } from 'react';
import { Paperclip, RefreshCw } from 'lucide-react';

import { buildAccountMap, formatDate, resolveAccount } from '../../../lib/formatters';
import Spinner from '../../../components/common/Spinner';
import Checkbox from '../../../components/common/Checkbox';
import FavoriteButton from './FavoriteButton';
import type { HeaderCheckboxState } from '../../../lib/hooks/useSelection';
import type { EmailMetadataOut, AccountOut } from '../../../api/types/dto';

type EmailTableView = 'individual' | 'unified';

type Props = {
  emails: EmailMetadataOut[];
  accounts: AccountOut[];
  loading: boolean;
  view: EmailTableView;
  isSent: boolean;
  hasSelection?: boolean;
  isSelected?: (email: EmailMetadataOut) => boolean;
  onToggle?: (email: EmailMetadataOut) => void;
  onToggleAll?: () => void;
  onOpen?: (email: EmailMetadataOut) => void;
  onToggleFavorite?: (email: EmailMetadataOut, next: boolean) => void;
  headerCheckboxState?: HeaderCheckboxState;
  bulkBar?: ReactNode;
  emptyMessage?: string;
};

// Column-visibility matrix tied to (view, isSent). Captures the bug-fix
// rules: individual mailboxes only need the "other" side of the message
// (the user's account email is always the same in DE/PARA otherwise),
// while unified mailboxes need both columns to disambiguate which of the
// user's accounts is involved.
function resolveColumnLayout(
  view: EmailTableView,
  isSent: boolean,
): {
  showTo: boolean;
  showFrom: boolean;
} {
  if (view === 'individual') {
    return { showTo: isSent, showFrom: !isSent };
  }
  return { showTo: true, showFrom: true };
}

export default function EmailTable({
  emails,
  accounts,
  loading,
  view,
  isSent,
  hasSelection = false,
  isSelected,
  onToggle,
  onToggleAll,
  onOpen,
  onToggleFavorite,
  headerCheckboxState = 'unchecked',
  bulkBar,
  emptyMessage,
}: Props) {
  const accountsById = useMemo(() => buildAccountMap(accounts), [accounts]);
  const { showTo, showFrom } = resolveColumnLayout(view, isSent);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const selectionEnabled = Boolean(isSelected && onToggle && onToggleAll);

  return (
    <div className="flex flex-col">
      <div className="flex h-11 items-center gap-4 border-b border-zinc-200 px-8">
        {hasSelection && bulkBar ? (
          bulkBar
        ) : (
          <>
            {selectionEnabled ? (
              <Checkbox
                state={headerCheckboxState}
                onClick={onToggleAll!}
                ariaLabel="Seleccionar los 50 correos más recientes"
              />
            ) : (
              <div className="h-[18px] w-[18px] rounded border-[1.5px] border-zinc-300" />
            )}
            <RefreshCw className="h-[18px] w-[18px] text-zinc-500" />
            <span className="text-[13px] font-medium text-zinc-500">{emails.length} correos</span>
          </>
        )}
      </div>

      <div className="flex h-8 items-center gap-3 border-b border-zinc-200 px-8 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
        <div className="w-[18px]" />
        <div className="w-5" aria-hidden />
        <div className="w-[120px]">Remitente</div>
        {showTo && <div className="w-[170px]">Para</div>}
        {showFrom && <div className="w-[170px]">De</div>}
        <div className="flex-1">Asunto</div>
        <div className="w-16 text-right">Fecha</div>
      </div>

      {emails.length === 0 ? (
        <div className="py-16 text-center text-sm text-zinc-400">
          {emptyMessage ?? 'No hay correos en esta bandeja'}
        </div>
      ) : (
        emails.map((email) => {
          const { providerName, accountEmail } = resolveAccount(email.account_id, accountsById);
          const unread = !email.is_read;
          const weight = unread ? 'font-semibold' : 'font-normal';
          const checked = isSelected?.(email) ?? false;
          const rowBg = checked ? 'bg-blue-50' : unread ? 'bg-zinc-200' : 'bg-white';

          // Cell values are decided per (view, isSent):
          //  - "Para" in a SENT view shows the real recipient (to_email);
          //    in a non-sent unified view it shows the user's own account
          //    (which is the inbox the message landed in).
          //  - "De" in a SENT view shows the user's own account (who sent
          //    it); otherwise it shows the message's actual sender.
          const toCell = isSent ? (email.to_email ?? '') : accountEmail;
          const fromCell = isSent ? accountEmail : email.from_email;

          const openable = Boolean(onOpen);
          return (
            <div
              key={`${email.account_id}|${email.provider_message_id}`}
              role={openable ? 'button' : undefined}
              tabIndex={openable ? 0 : undefined}
              onClick={openable ? () => onOpen!(email) : undefined}
              onKeyDown={
                openable
                  ? (e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        onOpen!(email);
                      }
                    }
                  : undefined
              }
              className={`flex h-11 items-center gap-3 border-b border-zinc-100 px-8 ${rowBg} ${openable ? 'cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-600' : ''}`}
            >
              {selectionEnabled ? (
                <Checkbox
                  state={checked ? 'checked' : 'unchecked'}
                  onClick={() => onToggle!(email)}
                  ariaLabel="Seleccionar correo"
                />
              ) : (
                <div className="h-[18px] w-[18px] rounded border-[1.5px] border-zinc-300" />
              )}
              {onToggleFavorite ? (
                <FavoriteButton
                  isFavorite={email.is_favorite}
                  onToggle={(next) => onToggleFavorite(email, next)}
                  size={18}
                />
              ) : (
                <div className="w-5" />
              )}
              <div className={`w-[120px] truncate text-[13px] ${weight} text-zinc-900`}>
                {providerName}
              </div>
              {showTo && (
                <div className={`w-[170px] truncate text-xs ${weight} text-zinc-900`}>{toCell}</div>
              )}
              {showFrom && (
                <div className={`w-[170px] truncate text-xs ${weight} text-zinc-900`}>
                  {fromCell}
                </div>
              )}
              <div
                className={`flex-1 flex items-center gap-1.5 truncate text-[13px] ${weight} text-zinc-900`}
              >
                {email.has_attachments ? (
                  <Paperclip
                    className="h-3.5 w-3.5 shrink-0 text-zinc-500"
                    aria-label="Tiene adjuntos"
                  />
                ) : null}
                <span className="truncate">{email.subject ?? '(Sin asunto)'}</span>
              </div>
              <div className={`w-16 text-right text-xs ${weight} text-zinc-900`}>
                {formatDate(email.received_at)}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
