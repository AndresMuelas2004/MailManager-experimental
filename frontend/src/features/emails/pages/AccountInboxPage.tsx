import { useEffect, useMemo } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';

import useEmailList from '../hooks/useEmailList';
import useEmailViewer from '../hooks/useEmailViewer';
import EmailTable from '../components/EmailTable';
import ViewerMount from '../components/ViewerMount';
import AccountTabs from '../../../components/ui/AccountTabs';
import SearchInput from '../components/SearchInput';
import SearchHelpPopover from '../components/SearchHelpPopover';
import useDebounce from '../hooks/useDebounce';
import { isGenericLabel } from '../../../lib/providers';
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

export default function AccountInboxPage({ box }: Props) {
  const { mailboxId, accountId } = useParams<{
    mailboxId: string;
    accountId: string;
  }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawQ = searchParams.get('q') ?? '';
  const debouncedQ = useDebounce(rawQ, SEARCH_DEBOUNCE_MS);
  const page = parsePageParam(searchParams);

  // Conversation mode: ``groupByThread=true`` (7th positional arg) makes the
  // listing return one row per thread. The row is read-only + open, so no
  // selection / favourite wiring is instantiated on this page.
  const { emails, accounts, total, pageSize, totalPages, loading, isPlaceholder, error } =
    useEmailList(mailboxId!, box, accountId!, debouncedQ, undefined, page, true);

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

  const combinedError = error || viewer.error;
  const basePath = `/m/${mailboxId}/account/${accountId}`;

  const handleSearchChange = (next: string) => {
    const params = new URLSearchParams(searchParams);
    if (next.length === 0) params.delete('q');
    else params.set('q', next);
    params.delete('page');
    setSearchParams(params, { replace: true });
  };

  const isSearching = debouncedQ.trim().length >= MIN_SEARCH_LENGTH;
  const emptyMessage = isSearching
    ? 'No se encontraron correos para tu búsqueda.'
    : 'No hay correos en esta bandeja';

  // Columns follow the EFFECTIVE box: when q carries a valid in:, every
  // returned row shares that box, so the single individual-view column must
  // flip to match it. The box sent to the backend stays the route's box —
  // the real override is applied server-side from q. This is cosmetic only.
  const isSent = (parseInOperator(debouncedQ) ?? box) === 'SENT';

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-col gap-2 px-8 pt-8 pb-2">
        <h1 className="text-[28px] font-bold tracking-tight text-zinc-900">{title}</h1>
        <p className="text-[15px] leading-[1.5] text-zinc-500">Correos de {title}</p>
      </div>

      <AccountTabs basePath={basePath} inboxLabel={bandejaLabel} />

      <div className="flex items-center gap-2 px-8 pt-4">
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
        view="individual"
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
