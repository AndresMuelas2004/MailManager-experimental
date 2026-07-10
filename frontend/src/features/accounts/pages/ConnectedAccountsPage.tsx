import { useParams } from 'react-router-dom';

import useConnectedAccounts from '../hooks/useConnectedAccounts';
import useBackfillStatus from '../hooks/useBackfillStatus';
import AddAccountCard from '../components/AddAccountCard';
import AccountCard from '../components/AccountCard';
import Spinner from '../../../components/common/Spinner';
import { useTranslation } from '../../../lib/i18n';

export default function ConnectedAccountsPage() {
  const { mailboxId } = useParams<{ mailboxId: string }>();
  const { t } = useTranslation();

  const {
    entries,
    loading,
    displayLabel,
    setDisplayLabel,
    selectedProvider,
    setSelectedProvider,
    canAdd,
    addingAccount,
    addAccount,
    removeAccount,
    reconnectAccount,
    editAccountLabel,
    error,
  } = useConnectedAccounts(mailboxId!);

  // Live backfill progress per account (server state). Accounts without a job
  // are simply absent from the Map → their card renders normally.
  const { statuses: backfillStatuses } = useBackfillStatus(mailboxId!);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8 px-4 pt-6 pb-6 lg:px-8 lg:pt-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-bold tracking-tight text-zinc-900 lg:text-[28px]">
          {t('accounts.title')}
        </h1>
        <p className="max-w-[600px] text-[15px] leading-[1.5] text-zinc-500">
          {t('accounts.subtitle')}
        </p>
      </div>

      {/* "Añadir cuenta" is a tall card fixed to the left; the thin account
          rows flow in a grid to its right (filling left-to-right, then down). */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        <div className="w-full lg:w-72 lg:shrink-0">
          <AddAccountCard
            displayLabel={displayLabel}
            onDisplayLabelChange={setDisplayLabel}
            selectedProvider={selectedProvider}
            onProviderChange={setSelectedProvider}
            onAdd={addAccount}
            canAdd={canAdd}
            adding={addingAccount}
          />
        </div>

        <div className="grid flex-1 content-start gap-2 [grid-template-columns:repeat(auto-fill,minmax(12rem,1fr))]">
          {entries.map((entry) => {
            const backfill = backfillStatuses.get(entry.account.account_id);
            return (
              <AccountCard
                key={entry.account.account_id}
                account={entry.account}
                status={entry.status}
                backfillStatus={backfill?.status}
                backfillFetchedCount={backfill?.fetched_count}
                onEditLabel={(label) => editAccountLabel(entry.account.account_id, label)}
                onReconnect={() => reconnectAccount(entry.account.account_id)}
                onDelete={() => removeAccount(entry.account.account_id)}
              />
            );
          })}
        </div>
      </div>

      {error && <p className="text-sm text-red-600">{error.message}</p>}
    </div>
  );
}
