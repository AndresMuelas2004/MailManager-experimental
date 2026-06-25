import { useState } from 'react';
import { useParams } from 'react-router-dom';

import useAccountSignatures from '../hooks/useAccountSignatures';
import AccountSignatureRow from '../components/AccountSignatureRow';
import Spinner from '../../../components/common/Spinner';
import { useTranslation } from '../../../lib/i18n';

export default function SignatureSettingsPage() {
  const { mailboxId } = useParams<{ mailboxId: string }>();
  const { t } = useTranslation();
  const { accounts, loading, error, savingAccountId, updateSignature } = useAccountSignatures(
    mailboxId!,
  );
  // Ephemeral "Firma guardada" confirmation, owned here so it survives the
  // row's key-remount (the row re-seeds from the sanitised value the backend
  // returns). Cleared when the row reports an edit.
  const [savedAccountId, setSavedAccountId] = useState<string | null>(null);

  const handleSave = async (accountId: string, signatureHtml: string) => {
    setSavedAccountId(null);
    const ok = await updateSignature(accountId, signatureHtml);
    if (ok) setSavedAccountId(accountId);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-col gap-2 px-4 pt-6 pb-6 lg:px-8 lg:pt-8">
        <h1 className="text-2xl font-bold tracking-tight text-zinc-900 lg:text-[28px]">
          {t('settings.signature.title')}
        </h1>
        <p className="text-[15px] leading-[1.5] text-zinc-500">
          {t('settings.signature.subtitle')}
        </p>
      </div>

      {error && <div className="px-8 pb-2 text-sm text-red-600">{error.message}</div>}

      {loading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : accounts.length === 0 ? (
        <div className="px-8 py-10 text-center text-sm text-zinc-400">
          {t('settings.signature.empty')}
        </div>
      ) : (
        <ul className="flex flex-col">
          {accounts.map((account) => (
            <AccountSignatureRow
              // Including the persisted signature in the key remounts the row
              // when a save returns a sanitised value, re-seeding the editor
              // with the stored HTML without a setState-in-effect.
              key={`${account.account_id}:${account.signature_html ?? ''}`}
              account={account}
              saving={savingAccountId === account.account_id}
              saved={savedAccountId === account.account_id}
              onSave={(signatureHtml) => handleSave(account.account_id, signatureHtml)}
              onDirty={() => setSavedAccountId(null)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
