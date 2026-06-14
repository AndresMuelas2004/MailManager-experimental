import { useParams, useSearchParams } from 'react-router-dom';

import { useEffect } from 'react';

import useEmailList from '../hooks/useEmailList';
import useEmailViewer from '../hooks/useEmailViewer';
import useBulkBar from '../hooks/useBulkBar';
import EmailTable from '../components/EmailTable';
import ViewerMount from '../components/ViewerMount';
import SearchInput from '../components/SearchInput';
import SearchHelpPopover from '../components/SearchHelpPopover';
import useDebounce from '../../../lib/hooks/useDebounce';
import { EMAIL_BOX_CONFIG } from '../boxes';
import { parsePageParam } from '../../../lib/pagination';
import { parseInOperator } from '../../../lib/searchOperators';
import { useDraftComposerContext } from '../../../app/providers/DraftComposerContext';
import type { EmailBox } from '../../../lib/types';
import type { EmailMetadataOut } from '../../../api/types/dto';

type Props = {
  box: EmailBox;
};

const SEARCH_DEBOUNCE_MS = 300;
const MIN_SEARCH_LENGTH = 2;

export default function UnifiedInboxPage({ box }: Props) {
  const { mailboxId } = useParams<{ mailboxId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawQ = searchParams.get('q') ?? '';
  const debouncedQ = useDebounce(rawQ, SEARCH_DEBOUNCE_MS);
  const page = parsePageParam(searchParams);

  // Conversation mode groups the listing by thread (``groupByThread=true``,
  // 7th positional arg): one row per thread, collapsed to its most-recent
  // message. Selection + bulk actions are wired below and act on that
  // representative message; the favourite star stays the read-only aggregated
  // thread indicator (not re-wired here).
  const { emails, accounts, total, pageSize, totalPages, loading, isPlaceholder, error, refresh } =
    useEmailList(mailboxId!, box, undefined, debouncedQ, undefined, page, true);
  const config = EMAIL_BOX_CONFIG[box];

  const { selection, bulkError, bulkBar } = useBulkBar({
    box,
    refresh,
    searchKey: debouncedQ,
  });

  const handlePageChange = (next: number) => {
    const params = new URLSearchParams(searchParams);
    if (next <= 1) params.delete('page');
    else params.set('page', String(next));
    setSearchParams(params);
  };

  // Re-clamp to the last valid page when the total shrinks below the
  // current page (e.g. after a background sync or a bulk delete). Guarded
  // by ``!loading && !isPlaceholder`` so it never fights an in-flight
  // fetch; ``totalPages`` floors at 1 so an emptied box lands on page 1.
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

  const combinedError = error || bulkError || viewer.error;

  const handleSearchChange = (next: string) => {
    const params = new URLSearchParams(searchParams);
    if (next.length === 0) params.delete('q');
    else params.set('q', next);
    // Changing the filter must reset to page 1 in the same update so the
    // URL never lands on a page that does not exist for the new filter.
    params.delete('page');
    setSearchParams(params, { replace: true });
  };

  const isSearching = debouncedQ.trim().length >= MIN_SEARCH_LENGTH;
  const emptyMessage = isSearching
    ? 'No se encontraron correos para tu búsqueda.'
    : 'No hay correos en esta bandeja';

  // Columns follow the EFFECTIVE box: a valid in: in q shifts the box of
  // every returned row, so unified columns must render with that sense. The
  // box sent to the backend is unchanged (the override is applied
  // server-side from q). Cosmetic only.
  const isSent = (parseInOperator(debouncedQ) ?? box) === 'SENT';

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-col gap-2 px-8 pt-8 pb-6">
        <h1 className="text-[28px] font-bold tracking-tight text-zinc-900">{config.title}</h1>
        <p className="text-[15px] leading-[1.5] text-zinc-500">{config.subtitle}</p>
        <div className="flex items-center gap-2 pt-2">
          <SearchInput value={rawQ} onChange={handleSearchChange} />
          <SearchHelpPopover />
        </div>
      </div>
      {combinedError ? (
        <div className="px-8 text-sm text-red-600">{combinedError.message}</div>
      ) : (
        <>
          <EmailTable
            emails={emails}
            accounts={accounts}
            loading={loading}
            view="unified"
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
        </>
      )}
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
