import { useParams, useSearchParams } from 'react-router-dom';

import useEmailList from '../hooks/useEmailList';
import useEmailViewer from '../hooks/useEmailViewer';
import useBulkBar from '../hooks/useBulkBar';
import useFavorite from '../hooks/useFavorite';
import EmailTable from '../components/EmailTable';
import ViewerMount from '../components/ViewerMount';
import SearchInput from '../components/SearchInput';
import useDebounce from '../hooks/useDebounce';
import { EMAIL_BOX_CONFIG } from '../boxes';
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

  const { emails, accounts, loading, error, refresh } = useEmailList(
    mailboxId!,
    box,
    undefined,
    debouncedQ,
  );
  const config = EMAIL_BOX_CONFIG[box];

  const { selection, bulkError, bulkBar } = useBulkBar({
    box,
    emails,
    refresh,
  });

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
    setSearchParams(params, { replace: true });
  };

  const isSearching = debouncedQ.trim().length >= MIN_SEARCH_LENGTH;
  const emptyMessage = isSearching
    ? 'No se encontraron correos para tu búsqueda.'
    : 'No hay correos en esta bandeja';

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-col gap-2 px-8 pt-8 pb-6">
        <h1 className="text-[28px] font-bold tracking-tight text-zinc-900">{config.title}</h1>
        <p className="text-[15px] leading-[1.5] text-zinc-500">{config.subtitle}</p>
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
          isSent={box === 'SENT'}
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
