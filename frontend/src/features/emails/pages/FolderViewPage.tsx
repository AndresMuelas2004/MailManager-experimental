import { useEffect } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';

import EmailTable from '../components/EmailTable';
import ViewerMount from '../components/ViewerMount';
import SearchInput, { MAX_SEARCH_LENGTH } from '../components/SearchInput';
import SearchHelpPopover from '../components/SearchHelpPopover';
import RefreshControl from '../components/RefreshControl';
import useEmailViewer from '../hooks/useEmailViewer';
import useEmailFolderControls from '../hooks/useEmailFolderControls';
import useFolder from '../hooks/useFolder';
import useFolderEmails from '../hooks/useFolderEmails';
import useDebounce from '../../../lib/hooks/useDebounce';
import { parsePageParam } from '../../../lib/pagination';
import { useTranslation } from '../../../lib/i18n';
import { useDraftComposerContext } from '../../../app/providers/DraftComposerContext';
import type { EmailMetadataOut } from '../../../api/types/dto';

const SEARCH_DEBOUNCE_MS = 300;
const MIN_SEARCH_LENGTH = 2;

// Lives in features/emails (NOT features/folders) because it reuses EmailTable /
// ViewerMount / the emails hooks — a feature cannot import from another feature
// (features §6). Same home as VirtualMailboxViewPage; the folder CRUD + rules
// stay in features/folders. Its data hooks (useFolder / useFolderEmails) live
// here too for the same reason.
export default function FolderViewPage() {
  const { mailboxId, folderId } = useParams<{ mailboxId: string; folderId: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawQ = (searchParams.get('q') ?? '').slice(0, MAX_SEARCH_LENGTH);
  const debouncedQ = useDebounce(rawQ, SEARCH_DEBOUNCE_MS);
  const page = parsePageParam(searchParams);
  const { record, error: loadError } = useFolder(folderId ?? '');

  // A folder listing is grouped by thread server-side (like the vmbox), so the
  // rows arrive as conversations (read-only + open).
  const {
    emails,
    accounts,
    total,
    pageSize,
    totalPages,
    loading,
    isPlaceholder,
    error,
    syncing,
    sync,
    lastSyncedAt,
  } = useFolderEmails(folderId ?? '', debouncedQ, page);

  // Per-email folder assignment from the listing row (menu) + membership chips.
  const folderControls = useEmailFolderControls();

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

  const combinedError = loadError || error || viewer.error;

  const handleSearchChange = (next: string) => {
    const params = new URLSearchParams(searchParams);
    if (next.length === 0) params.delete('q');
    else params.set('q', next);
    params.delete('page');
    setSearchParams(params, { replace: true });
  };

  const isSearching = debouncedQ.trim().length >= MIN_SEARCH_LENGTH;
  const emptyMessage = isSearching ? t('folders.emptySearch') : t('folders.emptyDefault');

  const isNotFound = loadError !== null && loadError.code === 'folder_not_found';

  if (isNotFound) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex flex-col gap-2 px-4 pt-6 pb-6 lg:px-8 lg:pt-8">
          <button
            type="button"
            onClick={() => navigate(`/m/${mailboxId}/folders`)}
            className="self-start text-xs text-zinc-500 hover:text-zinc-900"
          >
            {t('folders.notFoundBack')}
          </button>
        </div>
        <div className="mx-4 mt-8 rounded-md bg-zinc-50 px-6 py-10 text-center lg:mx-8">
          <h1 className="text-[20px] font-semibold text-zinc-900">{t('folders.notFoundTitle')}</h1>
          <p className="mt-2 text-[14px] text-zinc-500">{t('folders.notFoundDescription')}</p>
          <button
            type="button"
            onClick={() => navigate(`/m/${mailboxId}/folders`)}
            className="mt-4 inline-flex items-center rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
          >
            {t('folders.backToList')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-col gap-2 px-4 pt-6 pb-6 lg:px-8 lg:pt-8">
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-1.5">
            <button
              type="button"
              onClick={() => navigate(`/m/${mailboxId}/folders`)}
              className="self-start text-xs text-zinc-500 hover:text-zinc-900"
            >
              {t('folders.notFoundBack')}
            </button>
            <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight text-zinc-900 lg:text-[28px]">
              {record?.color ? (
                <span
                  className="h-4 w-4 shrink-0 rounded-full"
                  style={{ backgroundColor: record.color }}
                  aria-hidden
                />
              ) : null}
              {record?.name ?? t('folders.defaultName')}
            </h1>
            <p className="text-[15px] leading-[1.5] text-zinc-500">{t('folders.viewSubtitle')}</p>
          </div>
          <RefreshControl onRefresh={sync} syncing={syncing} lastSyncedAt={lastSyncedAt} />
        </div>
        <div className="flex items-center gap-2 pt-2">
          <SearchInput value={rawQ} onChange={handleSearchChange} />
          <SearchHelpPopover />
        </div>
      </div>
      {combinedError ? (
        <div className="px-8 text-sm text-red-600">{combinedError.message}</div>
      ) : (
        <EmailTable
          emails={emails}
          accounts={accounts}
          loading={loading}
          view="mixed"
          isSent={false}
          conversationMode
          onOpen={viewer.open}
          folders={folderControls.folders}
          onAssignFolder={folderControls.onAssignFolder}
          onUnassignFolder={folderControls.onUnassignFolder}
          isFolderBusy={folderControls.isFolderBusy}
          emptyMessage={emptyMessage}
          page={page}
          pageSize={pageSize}
          total={total}
          onPageChange={handlePageChange}
          paginationDisabled={loading || isPlaceholder}
        />
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
