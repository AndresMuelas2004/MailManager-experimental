import { useMemo } from 'react';
import type { ReactNode } from 'react';
import { Paperclip, RefreshCw, Star } from 'lucide-react';

import {
  buildAccountMap,
  formatDate,
  normaliseSubject,
  resolveAccount,
} from '../../../lib/formatters';
import { useTranslation } from '../../../lib/i18n';
import Spinner from '../../../components/common/Spinner';
import Checkbox from '../../../components/common/Checkbox';
import FavoriteButton from './FavoriteButton';
import EmailPagination from './EmailPagination';
import type { HeaderCheckboxState } from '../../../lib/hooks/useSelection';
import type { EmailMetadataOut, AccountOut } from '../../../api/types/dto';

type EmailTableView = 'individual' | 'unified' | 'mixed';

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
  // Per-row anti double-click guard for the favourite star: returns true while
  // that email's toggle is in flight, so the star is disabled until it
  // settles. Only the interactive star (the ``onToggleFavorite`` branch)
  // consults it; the read-only conversation indicator ignores it.
  isFavoritePending?: (email: EmailMetadataOut) => boolean;
  headerCheckboxState?: HeaderCheckboxState;
  bulkBar?: ReactNode;
  emptyMessage?: string;
  // Conversation (thread-grouped) mode: the row is read-only + open. The star
  // becomes a non-interactive thread indicator (aggregated ``is_favorite``)
  // and a message-count chip shows next to the subject when the thread has
  // more than one message in this box. Selection and the interactive favourite
  // button are disabled simply by the page not passing their props. Favoritos
  // leaves this false/absent and keeps the classic behaviour.
  conversationMode?: boolean;
  // Pagination is optional: when the four props below are provided the
  // header bar shows the "from–to de total" range on the left and the
  // page controls on the right. Omitting them keeps the legacy
  // "{n} correos" counter and renders no controls.
  page?: number;
  pageSize?: number;
  total?: number;
  onPageChange?: (page: number) => void;
  paginationDisabled?: boolean;
};

// Column-visibility matrix tied to (view, isSent). Captures the bug-fix
// rules: individual mailboxes only need the "other" side of the message
// (the user's account email is always the same in DE/PARA otherwise),
// while unified mailboxes need both columns to disambiguate which of the
// user's accounts is involved. The 'mixed' mode is used by listings that
// mix received + sent rows (e.g. Favoritos): both columns are shown and
// the cell values are decided per-row from ``email.box`` so a sent
// favourite shows its real recipient under PARA instead of degrading to
// the user's own account email.
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

// Group thousands with a dot ("1234" → "1.234") using a regex rather than
// ``Intl.NumberFormat`` because the grouping separator ``Intl`` emits
// depends on the runtime's ICU data (small-ICU Node / some CI images drop
// grouping for ``es-ES`` entirely), which would make the text
// non-deterministic.
function formatThousands(value: number): string {
  return value.toString().replace(/\B(?=(\d{3})+(?!\d))/g, '.');
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
  isFavoritePending,
  headerCheckboxState = 'unchecked',
  bulkBar,
  emptyMessage,
  conversationMode = false,
  page,
  pageSize,
  total,
  onPageChange,
  paginationDisabled,
}: Props) {
  const { t } = useTranslation();
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

  const hasPagination =
    page !== undefined &&
    pageSize !== undefined &&
    total !== undefined &&
    onPageChange !== undefined;

  // The left-hand label replaces the old "{n} correos" counter with the
  // pagination range when pagination is wired; an empty result collapses
  // to a plain "0 correos" instead of a nonsensical "1–0 de 0".
  let countLabel = t('emailTable.count', { count: emails.length });
  if (hasPagination) {
    if (total! === 0) {
      countLabel = t('emailTable.count', { count: 0 });
    } else {
      const offset = (page! - 1) * pageSize!;
      const from = offset + 1;
      const to = Math.min(offset + pageSize!, total!);
      countLabel = t('emailTable.rangeOf', {
        from: formatThousands(from),
        to: formatThousands(to),
        total: formatThousands(total!),
      });
    }
  }

  return (
    <div className="flex flex-col">
      <div className="sticky top-0 z-10 bg-[#F9FAFB]">
        <div className="flex h-11 items-center justify-between gap-4 border-b border-zinc-200 px-4 lg:px-8">
          {hasSelection && bulkBar ? (
            bulkBar
          ) : (
            <div className="flex items-center gap-4">
              {selectionEnabled ? (
                <Checkbox
                  state={headerCheckboxState}
                  onClick={onToggleAll!}
                  ariaLabel={t('emailTable.selectTopRecent')}
                />
              ) : (
                <div className="h-[18px] w-[18px]" />
              )}
              <RefreshCw className="h-[18px] w-[18px] text-zinc-500" />
              <span className="text-[13px] font-medium text-zinc-500">{countLabel}</span>
            </div>
          )}
          {/* Pager stays on the right in BOTH modes (count group OR bulk bar
              on the left) so a selection can be carried across pages — the
              selection Map in useBulkBar survives the page change. */}
          {hasPagination && total! > 0 && (
            <EmailPagination
              page={page!}
              pageSize={pageSize!}
              total={total!}
              onPageChange={onPageChange!}
              disabled={paginationDisabled}
            />
          )}
        </div>

        <div className="hidden h-8 items-center gap-3 border-b border-zinc-200 px-8 text-[11px] font-semibold uppercase tracking-wider text-zinc-400 lg:flex">
          <div className="w-[18px]" />
          <div className="w-5" aria-hidden />
          <div className="w-[120px]">{t('emailTable.colSender')}</div>
          {showTo && <div className="w-[170px]">{t('emailTable.colTo')}</div>}
          {showFrom && <div className="w-[170px]">{t('emailTable.colFrom')}</div>}
          <div className="flex-1">{t('emailTable.colSubject')}</div>
          <div className="w-16 text-right">{t('emailTable.colDate')}</div>
        </div>
      </div>

      {emails.length === 0 ? (
        <div className="py-16 text-center text-sm text-zinc-400">
          {emptyMessage ?? t('inbox.emptyDefault')}
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
          //
          // 'mixed' view ignores the table-level isSent and resolves the
          // cell sense per-row from ``email.box``: SENT rows put the real
          // recipient under PARA, every other row keeps the inbound
          // semantics. This is the only place that consults the row's box
          // directly — everywhere else the (view, isSent) matrix decides
          // for the whole table.
          const rowIsSent = view === 'mixed' ? email.box === 'SENT' : isSent;
          // In a SENT cell prefer the recipient email, then its display name;
          // when neither was captured (a message sent Bcc-only or without a To
          // header) fall back to a muted placeholder so the column never renders
          // blank.
          const toRecipient = email.to_email || email.to_name || '';
          const toMissing = rowIsSent && toRecipient === '';
          const toCell = rowIsSent ? toRecipient : accountEmail;
          const fromCell = rowIsSent ? accountEmail : email.from_email;

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
              className={`flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-zinc-100 px-4 py-2 ${rowBg} lg:h-11 lg:flex-nowrap lg:gap-3 lg:px-8 lg:py-0 ${openable ? 'cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-600' : ''}`}
            >
              {selectionEnabled ? (
                <Checkbox
                  state={checked ? 'checked' : 'unchecked'}
                  onClick={() => onToggle!(email)}
                  ariaLabel={t('emailTable.selectEmail')}
                />
              ) : (
                <div className="h-[18px] w-[18px]" />
              )}
              {onToggleFavorite ? (
                <FavoriteButton
                  isFavorite={email.is_favorite}
                  disabled={isFavoritePending?.(email) ?? false}
                  onToggle={(next) => onToggleFavorite(email, next)}
                  size={18}
                />
              ) : conversationMode ? (
                // Read-only thread indicator: filled amber when ANY message of
                // the thread is favourite (aggregated by the backend), faint
                // outline otherwise. Not a button — the whole row opens the
                // conversation; favourite actions live in the viewer.
                <div className="grid w-5 place-items-center" aria-hidden>
                  <Star
                    className={
                      email.is_favorite ? 'fill-amber-400 text-amber-400' : 'text-zinc-300'
                    }
                    style={{ width: 18, height: 18 }}
                    strokeWidth={1.75}
                  />
                </div>
              ) : (
                <div className="w-5" />
              )}
              <div
                className={`min-w-0 flex-1 truncate text-[13px] ${weight} text-zinc-900 lg:w-[120px] lg:flex-none`}
              >
                {providerName}
              </div>
              {showTo && (
                <div
                  className={`hidden w-[170px] truncate text-xs lg:block ${
                    toMissing ? 'font-normal italic text-zinc-400' : `${weight} text-zinc-900`
                  }`}
                >
                  {toMissing ? t('emailTable.noRecipient') : toCell}
                </div>
              )}
              {showFrom && (
                <div
                  className={`hidden w-[170px] truncate text-xs ${weight} text-zinc-900 lg:block`}
                >
                  {fromCell}
                </div>
              )}
              <div
                className={`order-last flex basis-full items-center gap-1.5 truncate text-[13px] ${weight} text-zinc-900 lg:order-none lg:basis-auto lg:flex-1`}
              >
                {conversationMode && email.thread_message_count > 1 ? (
                  <span
                    className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] font-medium text-zinc-500"
                    aria-label={t('emailTable.threadMessages', {
                      count: email.thread_message_count,
                    })}
                  >
                    {email.thread_message_count}
                  </span>
                ) : null}
                {email.has_attachments ? (
                  <Paperclip
                    className="h-3.5 w-3.5 shrink-0 text-zinc-500"
                    aria-label={t('emailTable.hasAttachments')}
                  />
                ) : null}
                <span className="truncate">
                  {/* Thread rows show the base subject without the Re:/Fwd:
                      prefix stack (docs/features/conversaciones.md § 3). */}
                  {conversationMode
                    ? normaliseSubject(email.subject)
                    : (email.subject ?? t('common.noSubject'))}
                </span>
              </div>
              <div className={`w-16 shrink-0 text-right text-xs ${weight} text-zinc-900`}>
                {formatDate(email.received_at)}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
