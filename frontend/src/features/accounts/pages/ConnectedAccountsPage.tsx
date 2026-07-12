import { useParams } from 'react-router-dom';

import useConnectedAccounts from '../hooks/useConnectedAccounts';
import useBackfillStatus from '../hooks/useBackfillStatus';
import useAccountQuota from '../hooks/useAccountQuota';
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

  // Per-user account quota (server state). When at the cap the "Add account"
  // button is disabled and an inline notice explains why; the 409 handled by
  // toUiError is the second-line defence for any path that reaches the backend.
  const quota = useAccountQuota();
  const effectiveCanAdd = canAdd && !quota.atLimit;

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
        {quota.connected !== undefined && quota.limit !== undefined && (
          <p className="text-sm font-medium text-zinc-600">
            {t('accounts.quotaCount', { connected: quota.connected, max: quota.limit })}
          </p>
        )}
        {quota.atLimit && quota.limit !== undefined && (
          <p className="text-sm text-amber-600">
            {t('accounts.limitReached', { max: quota.limit })}
          </p>
        )}
      </div>

      {/* Four-column grid: "Añadir cuenta" takes one column, two rows tall, and
          the account cards — same width, half the height — flow around it: three
          to its right on the first two rows, then four per row once they drop
          below it. Collapses to two columns (panel full-width) under lg. */}
      <div className="grid grid-cols-2 items-stretch gap-4 lg:grid-cols-4">
        <div className="col-span-2 flex lg:col-span-1 lg:row-span-2">
          <AddAccountCard
            displayLabel={displayLabel}
            onDisplayLabelChange={setDisplayLabel}
            selectedProvider={selectedProvider}
            onProviderChange={setSelectedProvider}
            onAdd={addAccount}
            canAdd={effectiveCanAdd}
            adding={addingAccount}
          />
        </div>

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

      {error && <p className="text-sm text-red-600">{error.message}</p>}
    </div>
  );
}
