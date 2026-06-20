import { RefreshCw } from 'lucide-react';

import useSyncAll from '../hooks/useSyncAll';
import { useTranslation } from '../../../lib/i18n';

export default function DataSyncPage() {
  const { t } = useTranslation();
  const { running, doneCount, totalCount, error, partialError, succeeded, syncAll } = useSyncAll();

  return (
    <div className="flex flex-col gap-8 px-8 pt-8 pb-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-[28px] font-bold tracking-tight text-zinc-900">
          {t('settings.data.title')}
        </h1>
        <p className="text-[15px] leading-[1.5] text-zinc-500">{t('settings.data.subtitle')}</p>
      </div>

      <div className="flex flex-col gap-3">
        <button
          type="button"
          onClick={() => void syncAll()}
          disabled={running}
          className="inline-flex w-fit items-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-md shadow-blue-600/25 transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <RefreshCw className={`h-4 w-4 ${running ? 'animate-spin' : ''}`} />
          {running ? t('settings.data.syncing') : t('settings.data.syncAllButton')}
        </button>

        {running && totalCount > 0 && (
          <p className="text-sm text-zinc-500" aria-live="polite">
            {t('settings.data.progress', { done: doneCount, total: totalCount })}
          </p>
        )}
        {!running && succeeded && (
          <p className="text-sm text-green-600" aria-live="polite">
            {t('settings.data.done')}
          </p>
        )}
        {!running && partialError && (
          <p className="text-sm text-amber-600" aria-live="polite">
            {t('settings.data.partialError')}
          </p>
        )}
        {error && <p className="text-sm text-red-600">{error.message}</p>}
      </div>
    </div>
  );
}
