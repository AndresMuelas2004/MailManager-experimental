import { useState } from 'react';

import Modal from '../../../components/common/Modal';
import { useTranslation } from '../../../lib/i18n';

type Props = {
  open: boolean;
  email: string;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
};

// Blocking, centred confirmation for the irreversible "delete account" action.
// The red button stays disabled until the typed text matches the user's own
// email EXACTLY — a single click is never enough (general description §3.1).
export default function DeleteUserDialog({ open, email, busy, onConfirm, onClose }: Props) {
  const { t } = useTranslation();
  const [typed, setTyped] = useState('');

  const matches = typed === email;

  return (
    <Modal
      open={open}
      onClose={onClose}
      ariaLabel={t('settings.account.deleteDialogAria')}
      widthClass="max-w-md"
    >
      <div className="flex flex-col gap-4 px-6 py-6 pr-12">
        <h2 className="text-lg font-semibold text-zinc-900">{t('settings.account.deleteTitle')}</h2>
        <p className="text-sm text-zinc-600">{t('settings.account.deleteWarning')}</p>
        <ul className="list-disc space-y-1 pl-5 text-sm text-zinc-600">
          <li>{t('settings.account.deleteItemMailboxes')}</li>
          <li>{t('settings.account.deleteItemAccounts')}</li>
          <li>{t('settings.account.deleteItemEmails')}</li>
        </ul>
        <label className="flex flex-col gap-1.5 text-sm text-zinc-700">
          {t('settings.account.deleteConfirmPrompt', { email })}
          <input
            type="email"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={t('settings.account.deleteConfirmPlaceholder')}
            autoComplete="off"
            className="h-10 rounded-lg border-[1.5px] border-zinc-200 px-3 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-red-500 focus:outline-none"
          />
        </label>
        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-lg border border-zinc-200 px-3 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-60"
          >
            {t('common.cancel')}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={!matches || busy}
            className="rounded-lg bg-red-600 px-3 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {t('settings.account.deleteConfirmButton')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
