import { Pencil, RefreshCw, Trash2 } from 'lucide-react';

import { useTranslation } from '../../../lib/i18n';

type Props = {
  onEditLabel?: () => void;
  onReconnect?: () => void;
  onDelete?: () => void;
};

export default function AccountCardDropdown({ onEditLabel, onReconnect, onDelete }: Props) {
  const { t } = useTranslation();

  return (
    <div className="absolute right-0 top-full z-30 mt-1 w-56 rounded-xl border border-zinc-200 bg-white py-1 shadow-lg">
      {onEditLabel && (
        <button
          type="button"
          onClick={onEditLabel}
          className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm text-zinc-700 hover:bg-zinc-50"
        >
          <Pencil className="h-4 w-4" />
          {t('accounts.editLabel')}
        </button>
      )}
      {onReconnect && (
        <button
          type="button"
          onClick={onReconnect}
          className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm text-zinc-700 hover:bg-zinc-50"
        >
          <RefreshCw className="h-4 w-4" />
          {t('accounts.reconnect')}
        </button>
      )}
      {onDelete && (
        <button
          type="button"
          onClick={onDelete}
          className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm text-red-600 hover:bg-red-50"
        >
          <Trash2 className="h-4 w-4" />
          {t('accounts.delete')}
        </button>
      )}
    </div>
  );
}
