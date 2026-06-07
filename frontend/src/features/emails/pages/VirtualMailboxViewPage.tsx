import { useEffect } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';

import EmailTable from '../components/EmailTable';
import ViewerMount from '../components/ViewerMount';
import SearchInput from '../components/SearchInput';
import useEmailViewer from '../hooks/useEmailViewer';
import useBulkBar from '../hooks/useBulkBar';
import useDebounce from '../hooks/useDebounce';
import useFavorite from '../hooks/useFavorite';
import useVirtualMailbox from '../hooks/useVirtualMailbox';
import useVirtualMailboxEmails from '../hooks/useVirtualMailboxEmails';
import { parsePageParam } from '../../../lib/pagination';
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
  const [searchParams, setSearchParams] = useSearchParams();
  const rawQ = searchParams.get('q') ?? '';
  const debouncedQ = useDebounce(rawQ, SEARCH_DEBOUNCE_MS);
  const page = parsePageParam(searchParams);
  const { record, error: loadError } = useVirtualMailbox(virtualMailboxId ?? '');

  const { emails, accounts, total, pageSize, totalPages, loading, isPlaceholder, error, refresh } =
    useVirtualMailboxEmails(virtualMailboxId!, mailboxId!, debouncedQ, page);

  // Bulk actions on a virtual mailbox view still operate on real emails
  // — the underlying email is a real provider message in a real
  // account. The same useBulkBar hook works because it keys by
  // (account_id, provider_message_id), which both views surface
  // identically. The visible "box" passed in is informational only
  // (controls which bulk actions are exposed); ALL_MAIL is the safest
  // baseline.
  const { selection, bulkError, bulkBar } = useBulkBar({
    box: 'ALL_MAIL',
    refresh,
    searchKey: debouncedQ,
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
  // ``useFavorite`` is no longer parameterised by mailboxId: the toggle
  // path passes ``email.mailbox_id`` per call (see ``handleToggleFavorite``
  // below), which is the only correct mailbox in a virtual-mailbox view
  // that may aggregate accounts across several real mailboxes. A sync
  // button is intentionally NOT exposed here — a vmbox-scoped sync would
  // need a fan-out over every (mailbox_id, account_id) pair the vmbox
  // covers, which is a separate user surface decision.
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

  const combinedError = error || bulkError || favorites.error || viewer.error;

  const handleSearchChange = (next: string) => {
    const params = new URLSearchParams(searchParams);
    if (next.length === 0) params.delete('q');
    else params.set('q', next);
    params.delete('page');
    setSearchParams(params, { replace: true });
  };

  const isSearching = debouncedQ.trim().length >= MIN_SEARCH_LENGTH;
  const emptyMessage = isSearching
    ? 'No se encontraron correos para tu búsqueda en esta bandeja ficticia.'
    : 'Ningún correo coincide con los filtros de esta bandeja ficticia.';

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
            ← Volver a bandejas ficticias
          </button>
        </div>
        <div className="mx-8 mt-8 rounded-md bg-zinc-50 px-6 py-10 text-center">
          <h1 className="text-[20px] font-semibold text-zinc-900">
            Esta bandeja ficticia ya no existe
          </h1>
          <p className="mt-2 text-[14px] text-zinc-500">
            Es posible que la hayas eliminado o que la URL sea incorrecta.
          </p>
          <button
            type="button"
            onClick={() => navigate(`/m/${mailboxId}/virtual-mailboxes`)}
            className="mt-4 inline-flex items-center rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
          >
            Volver al listado
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
              ← Volver a bandejas ficticias
            </button>
            <h1 className="text-[28px] font-bold tracking-tight text-zinc-900">
              {record?.display_name ?? 'Bandeja ficticia'}
            </h1>
            <p className="text-[15px] leading-[1.5] text-zinc-500">
              Vista filtrada — los correos siguen viviendo en sus bandejas reales.
            </p>
          </div>
        </div>
        <div className="pt-2">
          <SearchInput value={rawQ} onChange={handleSearchChange} />
        </div>
      </div>
      {loadError && <div className="px-8 text-sm text-red-600">{loadError.message}</div>}
      {combinedError ? (
        <div className="px-8 text-sm text-red-600">{combinedError.message}</div>
      ) : (
        <>
          <EmailTable
            emails={emails}
            accounts={accounts}
            loading={loading}
            view="unified"
            isSent={record?.filter_payload?.box === 'SENT'}
            hasSelection={selection.size > 0}
            isSelected={selection.isSelected}
            onToggle={selection.toggle}
            onToggleAll={() => selection.toggleTopN(emails)}
            onOpen={viewer.open}
            onToggleFavorite={handleToggleFavorite}
            headerCheckboxState={selection.headerState(emails)}
            bulkBar={bulkBar}
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
        onClose={viewer.close}
        onRead={viewer.handleRead}
        onReply={handleReply}
        onReplyAll={handleReplyAll}
        onForward={handleForward}
      />
    </div>
  );
}
