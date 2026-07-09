import { useEffect, useMemo } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';

import useEmailList from '../hooks/useEmailList';
import useEmailViewer from '../hooks/useEmailViewer';
import useBulkBar from '../hooks/useBulkBar';
import EmailTable from '../components/EmailTable';
import ViewerMount from '../components/ViewerMount';
import SearchInput, { MAX_SEARCH_LENGTH } from '../components/SearchInput';
import SearchHelpPopover from '../components/SearchHelpPopover';
import RefreshControl from '../components/RefreshControl';
import ListControls from '../components/ListControls';
import useDebounce from '../../../lib/hooks/useDebounce';
import { isGenericLabel } from '../../../lib/providers';
import { parsePageParam } from '../../../lib/pagination';
import { parseInOperator } from '../../../lib/searchOperators';
import { parseListControls, writeListControls, isAnyFilterActive } from '../../../lib/listControls';
import { useTranslation } from '../../../lib/i18n';
import { useDraftComposerContext } from '../../../app/providers/DraftComposerContext';
import type { EmailBox } from '../../../lib/types';
import type { EmailMetadataOut } from '../../../api/types/dto';
import type { ListControlsState } from '../../../lib/listControls';

type Props = {
  box: EmailBox;
};

const SEARCH_DEBOUNCE_MS = 300;
const MIN_SEARCH_LENGTH = 2;

export default function AccountInboxPage({ box }: Props) {
  const { mailboxId, accountId } = useParams<{
    mailboxId: string;
    accountId: string;
  }>();
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawQ = (searchParams.get('q') ?? '').slice(0, MAX_SEARCH_LENGTH);
  const debouncedQ = useDebounce(rawQ, SEARCH_DEBOUNCE_MS);
  const page = parsePageParam(searchParams);
  const controls = parseListControls(searchParams);

  // Conversation mode groups the listing by thread (``groupByThread=true``,
  // 7th positional arg): one row per thread, collapsed to its most-recent
  // message. Selection + bulk actions are wired below and act on that
  // representative message (the row's ``provider_message_id``); the favourite
  // star stays the read-only aggregated thread indicator (not re-wired here).
  const {
    emails,
    accounts,
    total,
    pageSize,
    totalPages,
    loading,
    isPlaceholder,
    error,
    refresh,
    sync,
    syncing,
    lastSyncedAt,
    syncError,
  } = useEmailList(mailboxId!, box, accountId!, debouncedQ, undefined, page, true, controls);

  const { selection, bulkError, bulkBar } = useBulkBar({
    box,
    refresh,
    searchKey: debouncedQ,
    scopeKey: `${mailboxId}:${accountId}:${box}`,
  });

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

  const title = useMemo(() => {
    const account = accounts.find((a) => a.account_id === accountId);
    const hasCustomLabel = account
      ? !isGenericLabel(account.display_label, account.provider)
      : false;
    const email = account?.email_address ?? account?.display_label ?? accountId!;
    return hasCustomLabel && account ? `${account.display_label} - ${email}` : email;
  }, [accounts, accountId]);

  const combinedError = error || bulkError || viewer.error;

  const handleSearchChange = (next: string) => {
    const params = new URLSearchParams(searchParams);
    if (next.length === 0) params.delete('q');
    else params.set('q', next);
    params.delete('page');
    setSearchParams(params, { replace: true });
  };

  const handleControlsChange = (next: ListControlsState) => {
    const params = new URLSearchParams(searchParams);
    // writeListControls also deletes ``page`` so a control change resets to
    // page 1 in the same URL update, mirroring handleSearchChange. Uses
    // ``replace: true`` so browser Back does not unwind filter-by-filter.
    writeListControls(params, next);
    setSearchParams(params, { replace: true });
  };

  const isSearching = debouncedQ.trim().length >= MIN_SEARCH_LENGTH;
  const isFiltering = isAnyFilterActive(controls);
  const emptyMessage =
    isSearching || isFiltering ? t('inbox.emptyFiltered') : t('inbox.emptyDefault');

  // Columns follow the EFFECTIVE box: when q carries a valid in:, every
  // returned row shares that box, so the single individual-view column must
  // flip to match it. The box sent to the backend stays the route's box —
  // the real override is applied server-side from q. This is cosmetic only.
  const isSent = (parseInOperator(debouncedQ) ?? box) === 'SENT';

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start justify-between gap-4 px-4 pt-6 pb-2 lg:px-8 lg:pt-8">
        <div className="flex flex-col gap-2">
          <h1 className="text-2xl font-bold tracking-tight text-zinc-900 lg:text-[28px]">
            {title}
          </h1>
          <p className="text-[15px] leading-[1.5] text-zinc-500">
            {t('inbox.accountSubtitle', { title })}
          </p>
        </div>
        <RefreshControl
          onRefresh={sync}
          syncing={syncing}
          lastSyncedAt={lastSyncedAt}
          hasError={Boolean(syncError)}
        />
      </div>

      <div className="flex items-center gap-2 px-4 pt-4 lg:px-8">
        <SearchInput value={rawQ} onChange={handleSearchChange} />
        <SearchHelpPopover />
      </div>

      <div className="px-4 pt-3 lg:px-8">
        <ListControls value={controls} onChange={handleControlsChange} />
      </div>

      {combinedError && (
        <div className="px-8 pt-4 text-sm text-red-600">{combinedError.message}</div>
      )}

      <EmailTable
        emails={emails}
        accounts={accounts}
        loading={loading}
        view="individual"
        isSent={isSent}
        conversationMode
        hasSelection={selection.size > 0}
        isSelected={selection.isSelected}
        onToggle={selection.toggle}
        onToggleAll={() => selection.toggleTopN(emails)}
        headerCheckboxState={selection.headerState(emails)}
        bulkBar={bulkBar}
        onOpen={viewer.open}
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
        conversationMode
        onClose={viewer.close}
        onRead={viewer.handleRead}
        onReply={handleReply}
        onReplyAll={handleReplyAll}
        onForward={handleForward}
      />
    </div>
  );
}
