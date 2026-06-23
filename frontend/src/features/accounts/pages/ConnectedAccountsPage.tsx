import { useNavigate, useParams } from 'react-router-dom';

import useConnectedAccounts from '../hooks/useConnectedAccounts';
import AddAccountCard from '../components/AddAccountCard';
import AccountCard from '../components/AccountCard';
import Spinner from '../../../components/common/Spinner';
import { useTranslation } from '../../../lib/i18n';

export default function ConnectedAccountsPage() {
  const { mailboxId } = useParams<{ mailboxId: string }>();
  const navigate = useNavigate();
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

      <div className="flex flex-col gap-4 sm:flex-row sm:flex-wrap">
        <div className="w-full max-w-none sm:max-w-[240px]">
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

        {entries.map((entry) => (
          <div key={entry.account.account_id} className="w-full max-w-none sm:max-w-[280px]">
            <AccountCard
              account={entry.account}
              emails={entry.emails}
              status={entry.status}
              onClick={() => navigate(`/m/${mailboxId}/account/${entry.account.account_id}`)}
              onEditLabel={(label) => editAccountLabel(entry.account.account_id, label)}
              onReconnect={() => reconnectAccount(entry.account.account_id)}
              onDelete={() => removeAccount(entry.account.account_id)}
            />
          </div>
        ))}
      </div>

      {error && <p className="text-sm text-red-600">{error.message}</p>}
    </div>
  );
}
