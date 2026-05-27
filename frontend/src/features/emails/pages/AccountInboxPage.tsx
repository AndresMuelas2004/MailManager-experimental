import { useMemo } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';

import useEmailList from '../hooks/useEmailList';
import useEmailViewer from '../hooks/useEmailViewer';
import useBulkBar from '../hooks/useBulkBar';
import useFavorite from '../hooks/useFavorite';
import EmailTable from '../components/EmailTable';
import ViewerMount from '../components/ViewerMount';
import AccountTabs from '../../../components/ui/AccountTabs';
import SearchInput from '../components/SearchInput';
import useDebounce from '../hooks/useDebounce';
import { isGenericLabel } from '../../../lib/providers';
import { useDraftComposerContext } from '../../../app/providers/DraftComposerContext';
import type { EmailBox } from '../../../lib/types';
import type { EmailMetadataOut } from '../../../api/types/dto';

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
  const [searchParams, setSearchParams] = useSearchParams();
  const rawQ = searchParams.get('q') ?? '';
  const debouncedQ = useDebounce(rawQ, SEARCH_DEBOUNCE_MS);

  const { emails, accounts, loading, error, refresh } = useEmailList(
    mailboxId!,
    box,
    accountId!,
    debouncedQ,
  );

  const { selection, bulkError, bulkBar } = useBulkBar({
    box,
    emails,
    refresh,
  });

  const viewer = useEmailViewer(refresh);
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
        mailboxId: email.mailbox_id,
        accountId: email.account_id,
        providerMessageId: email.provider_message_id,
        favorite: next,
      })
      .catch(() => {});
  };

  const { title, bandejaLabel } = useMemo(() => {
    const account = accounts.find((a) => a.account_id === accountId);
    const hasCustomLabel = account
      ? !isGenericLabel(account.display_label, account.provider)
      : false;
    const email = account?.email_address ?? account?.display_label ?? accountId!;
    const computedTitle = hasCustomLabel && account ? `${account.display_label} - ${email}` : email;
    const computedBandeja =
      hasCustomLabel && account ? `Bandeja ${account.display_label}` : `Bandeja ${email}`;
    return { title: computedTitle, bandejaLabel: computedBandeja };
  }, [accounts, accountId]);

  const combinedError = error || bulkError || favorites.error;
  const basePath = `/m/${mailboxId}/account/${accountId}`;

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
      <div className="flex flex-col gap-2 px-8 pt-8 pb-2">
        <h1 className="text-[28px] font-bold tracking-tight text-zinc-900">{title}</h1>
        <p className="text-[15px] leading-[1.5] text-zinc-500">Correos de {title}</p>
      </div>

      <AccountTabs basePath={basePath} inboxLabel={bandejaLabel} />

      <div className="px-8 pt-4">
        <SearchInput value={rawQ} onChange={handleSearchChange} />
      </div>

      {combinedError && (
        <div className="px-8 pt-4 text-sm text-red-600">{combinedError.message}</div>
      )}

      <EmailTable
        emails={emails}
        accounts={accounts}
        loading={loading}
        view="individual"
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
