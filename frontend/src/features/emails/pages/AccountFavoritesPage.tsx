import { useEffect, useMemo } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { RefreshCw } from 'lucide-react';

import useEmailList from '../hooks/useEmailList';
import useEmailViewer from '../hooks/useEmailViewer';
import useBulkBar from '../hooks/useBulkBar';
import useFavorite from '../hooks/useFavorite';
import useAccountUnreadCounts from '../hooks/useAccountUnreadCounts';
import EmailTable from '../components/EmailTable';
import ViewerMount from '../components/ViewerMount';
import AccountTabs from '../../../components/ui/AccountTabs';
import SearchInput from '../components/SearchInput';
import SearchHelpPopover from '../components/SearchHelpPopover';
import useDebounce from '../../../lib/hooks/useDebounce';
import { isGenericLabel } from '../../../lib/providers';
import { parsePageParam } from '../../../lib/pagination';
import { useTranslation } from '../../../lib/i18n';
import { useDraftComposerContext } from '../../../app/providers/DraftComposerContext';
import type { EmailMetadataOut } from '../../../api/types/dto';

const SEARCH_DEBOUNCE_MS = 300;
const MIN_SEARCH_LENGTH = 2;

export default function AccountFavoritesPage() {
  const { mailboxId, accountId } = useParams<{ mailboxId: string; accountId: string }>();
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawQ = searchParams.get('q') ?? '';
  const debouncedQ = useDebounce(rawQ, SEARCH_DEBOUNCE_MS);
  const page = parsePageParam(searchParams);

  // Same call shape as FavoritesPage but scoped to one account: box ALL_MAIL
  // anchor (server excludes TRASH/SPAM under favorite=true), favorite=true, and
  // NO 7th arg → group_by_thread stays false (favourites never group, so the
  // row star is clickable rather than a read-only thread indicator).
  const { emails, accounts, total, pageSize, totalPages, loading, isPlaceholder, error, refresh } =
    useEmailList(mailboxId!, 'ALL_MAIL', accountId!, debouncedQ, true, page);

  const { selection, bulkError, bulkBar } = useBulkBar({
    box: 'ALL_MAIL',
    refresh,
    searchKey: debouncedQ,
  });

  const { inboxUnread, spamUnread } = useAccountUnreadCounts(mailboxId!, accountId!);

  const handlePageChange = (next: number) => {
    const params = new URLSearchParams(searchParams);
    if (next <= 1) params.delete('page');
    else params.set('page', String(next));
    setSearchParams(params);
  };

  useEffect(() => {
    if (!loading && !isPlaceholder && page > totalPages) handlePageChange(totalPages);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, totalPages, loading, isPlaceholder]);

  const viewer = useEmailViewer();
  const favorites = useFavorite();
  const composer = useDraftComposerContext();

  const handleReply = (email: EmailMetadataOut) => {
    viewer.close();
    void composer.openForReply(email);
  };
  const handleReplyAll = (email: EmailMetadataOut) => {
    viewer.close();
    void composer.openForReplyAll(email);
  };
  const handleForward = (email: EmailMetadataOut) => {
    viewer.close();
    void composer.openForForward(email);
  };

  // Routes the PATCH to the email's REAL mailbox (email.mailbox_id), not the
  // route mailbox — identical to FavoritesPage. Optimistic flip + revert lives
  // in useFavorite.
  const handleToggleFavorite = (email: EmailMetadataOut, next: boolean) => {
    favorites
      .toggle({
        mailboxId: email.mailbox_id,
        accountId: email.account_id,
        providerMessageId: email.provider_message_id,
        favorite: next,
      })
      .catch(() => {});
  };

  // Per-row anti double-click guard: disable only the star whose toggle is in
  // flight, not the whole table (a single ``useFavorite`` instance drives every
  // row, so the global ``toggling`` would disable all stars at once).
  const isFavoritePending = (email: EmailMetadataOut) =>
    favorites.isToggling(email.account_id, email.provider_message_id);

  // Sync scoped to THIS account — the endpoint reconciles only it.
  const handleSync = () => {
    favorites.sync({ mailboxId: mailboxId!, accountId: accountId! }).catch(() => {});
  };

  const combinedError = error || bulkError || favorites.error || viewer.error;

  const handleSearchChange = (next: string) => {
    const params = new URLSearchParams(searchParams);
    if (next.length === 0) params.delete('q');
    else params.set('q', next);
    params.delete('page');
    setSearchParams(params, { replace: true });
  };

  // Account title, same computation as AccountInboxPage / AccountDraftsPage.
  const { title, bandejaLabel } = useMemo(() => {
    const account = accounts.find((a) => a.account_id === accountId);
    const hasCustomLabel = account
      ? !isGenericLabel(account.display_label, account.provider)
      : false;
    const email = account?.email_address ?? account?.display_label ?? accountId!;
    const computedTitle = hasCustomLabel && account ? `${account.display_label} - ${email}` : email;
    const labelBase = hasCustomLabel && account ? account.display_label : email;
    return {
      title: computedTitle,
      bandejaLabel: t('inbox.inboxLabelPrefix', { label: labelBase }),
    };
  }, [accounts, accountId, t]);

  const basePath = `/m/${mailboxId}/account/${accountId}`;

  const isSearching = debouncedQ.trim().length >= MIN_SEARCH_LENGTH;
  const emptyMessage = isSearching
    ? t('favorites.emptySearch')
    : t('favorites.emptyDefaultAccount');

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-col gap-2 px-4 pt-6 pb-2 lg:px-8 lg:pt-8">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
          <div className="flex flex-col gap-1.5">
            <h1 className="text-2xl font-bold tracking-tight text-zinc-900 lg:text-[28px]">
              {title}
            </h1>
            <p className="text-[15px] leading-[1.5] text-zinc-500">
              {t('favorites.accountSubtitle', { title })}
            </p>
          </div>
          <div className="flex flex-col items-end gap-1">
            <button
              type="button"
              onClick={handleSync}
              disabled={favorites.syncing}
              className="inline-flex items-center gap-2 rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${favorites.syncing ? 'animate-spin' : ''}`} />
              {favorites.syncing ? t('favorites.syncing') : t('favorites.sync')}
            </button>
            {favorites.syncError && (
              <span className="text-[12px] text-red-600" aria-live="polite">
                {t('common.syncFailed')}
              </span>
            )}
          </div>
        </div>
      </div>

      <AccountTabs
        basePath={basePath}
        inboxLabel={bandejaLabel}
        inboxUnread={inboxUnread}
        spamUnread={spamUnread}
      />

      <div className="flex items-center gap-2 px-4 pt-4 lg:px-8">
        <SearchInput value={rawQ} onChange={handleSearchChange} />
        <SearchHelpPopover />
      </div>

      {combinedError && (
        <div className="px-8 pt-4 text-sm text-red-600">{combinedError.message}</div>
      )}

      <EmailTable
        emails={emails}
        accounts={accounts}
        loading={loading}
        view="mixed"
        isSent={false}
        hasSelection={selection.size > 0}
        isSelected={selection.isSelected}
        onToggle={selection.toggle}
        onToggleAll={() => selection.toggleTopN(emails)}
        onOpen={viewer.open}
        onToggleFavorite={handleToggleFavorite}
        isFavoritePending={isFavoritePending}
        headerCheckboxState={selection.headerState(emails)}
        bulkBar={bulkBar}
        emptyMessage={emptyMessage}
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={handlePageChange}
        paginationDisabled={loading || isPlaceholder}
      />

      <ViewerMount
        openedEmail={viewer.openedEmail}
        accounts={accounts}
        onClose={viewer.close}
        onRead={viewer.handleRead}
        onReply={handleReply}
        onReplyAll={handleReplyAll}
        onForward={handleForward}
      />
    </div>
  );
}
