import { useParams, useSearchParams } from 'react-router-dom';
import { RefreshCw } from 'lucide-react';

import useEmailList from '../hooks/useEmailList';
import useEmailViewer from '../hooks/useEmailViewer';
import useBulkBar from '../hooks/useBulkBar';
import useFavorite from '../hooks/useFavorite';
import EmailTable from '../components/EmailTable';
import ViewerMount from '../components/ViewerMount';
import SearchInput from '../components/SearchInput';
import useDebounce from '../hooks/useDebounce';
import { useDraftComposerContext } from '../../../app/providers/DraftComposerContext';
import type { EmailMetadataOut } from '../../../api/types/dto';

const SEARCH_DEBOUNCE_MS = 300;
const MIN_SEARCH_LENGTH = 2;

export default function FavoritesPage() {
  const { mailboxId } = useParams<{ mailboxId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawQ = searchParams.get('q') ?? '';
  const debouncedQ = useDebounce(rawQ, SEARCH_DEBOUNCE_MS);

  // Box ALL_MAIL acts as the "everywhere except trash and spam" anchor
  // — the favourites filter overrides this server-side (TRASH/SPAM are
  // excluded automatically unless the user passes one of them) but the
  // useEmailList hook still needs SOME box param for query-key
  // stability across components.
  const { emails, accounts, loading, error, refresh } = useEmailList(
    mailboxId!,
    'ALL_MAIL',
    undefined,
    debouncedQ,
    true,
  );

  const { selection, bulkError, bulkBar } = useBulkBar({
    mailboxId: mailboxId!,
    box: 'ALL_MAIL',
    emails,
    refresh,
  });

  const viewer = useEmailViewer(mailboxId!, refresh);
  const favorites = useFavorite(mailboxId!);
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
        accountId: email.account_id,
        providerMessageId: email.provider_message_id,
        favorite: next,
      })
      .catch(() => {});
  };

  const handleSync = () => {
    favorites.sync().catch(() => {});
  };

  const combinedError = error || bulkError || favorites.error;

  const handleSearchChange = (next: string) => {
    const params = new URLSearchParams(searchParams);
    if (next.length === 0) params.delete('q');
    else params.set('q', next);
    setSearchParams(params, { replace: true });
  };

  const isSearching = debouncedQ.trim().length >= MIN_SEARCH_LENGTH;
  const emptyMessage = isSearching
    ? 'No se encontraron correos favoritos para tu búsqueda.'
    : 'Aún no has marcado ningún correo como favorito.';

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-col gap-2 px-8 pt-8 pb-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-1.5">
            <h1 className="text-[28px] font-bold tracking-tight text-zinc-900">Favoritos</h1>
            <p className="text-[15px] leading-[1.5] text-zinc-500">
              Correos marcados con estrella en Gmail o con bandera en Outlook.
            </p>
          </div>
          <button
            type="button"
            onClick={handleSync}
            disabled={favorites.syncing}
            className="inline-flex items-center gap-2 rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${favorites.syncing ? 'animate-spin' : ''}`} />
            {favorites.syncing ? 'Sincronizando…' : 'Sincronizar favoritos'}
          </button>
        </div>
        <div className="pt-2">
          <SearchInput value={rawQ} onChange={handleSearchChange} />
        </div>
      </div>
      {combinedError ? (
        <div className="px-8 text-sm text-red-600">{combinedError.message}</div>
      ) : (
        <EmailTable
          emails={emails}
          accounts={accounts}
          loading={loading}
          view="unified"
          isSent={false}
          hasSelection={selection.size > 0}
          isSelected={selection.isSelected}
          onToggle={selection.toggle}
          onToggleAll={() => selection.toggleTopN(emails)}
          onOpen={viewer.open}
          onToggleFavorite={handleToggleFavorite}
          headerCheckboxState={selection.headerState(emails)}
          bulkBar={bulkBar}
          emptyMessage={emptyMessage}
        />
      )}
      <ViewerMount
        mailboxId={mailboxId!}
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
