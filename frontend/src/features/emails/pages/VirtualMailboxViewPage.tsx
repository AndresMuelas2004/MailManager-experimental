import { useEffect } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { RefreshCw } from 'lucide-react';

import EmailTable from '../components/EmailTable';
import ViewerMount from '../components/ViewerMount';
import SearchInput from '../components/SearchInput';
import SearchHelpPopover from '../components/SearchHelpPopover';
import useEmailViewer from '../hooks/useEmailViewer';
import useDebounce from '../../../lib/hooks/useDebounce';
import useVirtualMailbox from '../hooks/useVirtualMailbox';
import useVirtualMailboxEmails from '../hooks/useVirtualMailboxEmails';
import { parsePageParam } from '../../../lib/pagination';
import { parseInOperator } from '../../../lib/searchOperators';
import { useTranslation } from '../../../lib/i18n';
import { useDraftComposerContext } from '../../../app/providers/DraftComposerContext';
import type { EmailMetadataOut } from '../../../api/types/dto';

const SEARCH_DEBOUNCE_MS = 300;
const MIN_SEARCH_LENGTH = 2;

export default function VirtualMailboxViewPage() {
  const { mailboxId, virtualMailboxId } = useParams<{
    mailboxId: string;
    virtualMailboxId: string;
  }>();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawQ = searchParams.get('q') ?? '';
  const debouncedQ = useDebounce(rawQ, SEARCH_DEBOUNCE_MS);
  const page = parsePageParam(searchParams);
  const { record, error: loadError } = useVirtualMailbox(virtualMailboxId ?? '');

  // The virtual listing is always grouped by thread server-side, so the rows
  // arrive as conversations. Conversation mode makes each row read-only +
  // open; no selection / favourite wiring on this page.
  const { emails, accounts, total, pageSize, totalPages, loading, isPlaceholder, error, syncing } =
    useVirtualMailboxEmails(
      virtualMailboxId!,
      mailboxId!,
      record?.account_ids ?? [],
      debouncedQ,
      page,
    );

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

  // Fold the vmbox-record load error into the single error banner. A 404 is
  // already short-circuited by the isNotFound early-return below, so a
  // loadError reaching the render here is a non-404 failure that must REPLACE
  // the table — not stack a second banner above a still-rendered EmailTable.
  const combinedError = loadError || error || viewer.error;

  const handleSearchChange = (next: string) => {
    const params = new URLSearchParams(searchParams);
    if (next.length === 0) params.delete('q');
    else params.set('q', next);
    params.delete('page');
    setSearchParams(params, { replace: true });
  };

  const isSearching = debouncedQ.trim().length >= MIN_SEARCH_LENGTH;
  const emptyMessage = isSearching
    ? t('virtualMailboxes.emptySearch')
    : t('virtualMailboxes.emptyDefault');

  // Columns follow the EFFECTIVE box. A valid in: in q intersects the
  // vmbox filter server-side and makes every returned row share that box,
  // so columns must render with that sense; otherwise fall back to the
  // vmbox's saved box. ``filter_payload.box`` is typed ``unknown`` (the OUT
  // schema is ``z.record(z.string(), z.unknown())``), and ``unknown ===
  // 'SENT'`` is legal — keep the comparison direct rather than narrowing to
  // an intermediate ``EmailBox``, which would not type-check.
  const inBox = parseInOperator(debouncedQ);
  const isSent = inBox !== null ? inBox === 'SENT' : record?.filter_payload?.box === 'SENT';

  // 404 on the vmbox lookup means the URL points to a deleted /
  // foreign / never-existed virtual mailbox. The listing hook will
  // independently also 404, which used to render the same red banner
  // twice (load + combined). Detect the lookup 404 and short-circuit
  // to a dedicated empty state so we don't show a stale title, an
  // active search input and a duplicated error.
  const isNotFound = loadError !== null && loadError.code === 'virtual_mailbox_not_found';

  if (isNotFound) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex flex-col gap-2 px-8 pt-8 pb-6">
          <button
            type="button"
            onClick={() => navigate(`/m/${mailboxId}/virtual-mailboxes`)}
            className="self-start text-xs text-zinc-500 hover:text-zinc-900"
          >
            {t('virtualMailboxes.notFoundBack')}
          </button>
        </div>
        <div className="mx-8 mt-8 rounded-md bg-zinc-50 px-6 py-10 text-center">
          <h1 className="text-[20px] font-semibold text-zinc-900">
            {t('virtualMailboxes.notFoundTitle')}
          </h1>
          <p className="mt-2 text-[14px] text-zinc-500">
            {t('virtualMailboxes.notFoundDescription')}
          </p>
          <button
            type="button"
            onClick={() => navigate(`/m/${mailboxId}/virtual-mailboxes`)}
            className="mt-4 inline-flex items-center rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
          >
            {t('virtualMailboxes.backToList')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-col gap-2 px-8 pt-8 pb-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-1.5">
            <button
              type="button"
              onClick={() => navigate(`/m/${mailboxId}/virtual-mailboxes`)}
              className="self-start text-xs text-zinc-500 hover:text-zinc-900"
            >
              {t('virtualMailboxes.notFoundBack')}
            </button>
            <h1 className="text-[28px] font-bold tracking-tight text-zinc-900">
              {record?.display_name ?? t('virtualMailboxes.defaultName')}
            </h1>
            <p className="text-[15px] leading-[1.5] text-zinc-500">
              {t('virtualMailboxes.viewSubtitle')}
            </p>
            {syncing && (
              <span className="inline-flex items-center gap-2 text-[13px] font-medium text-zinc-500">
                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                {t('common.syncing')}
              </span>
            )}
          </div>
        </div>
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
