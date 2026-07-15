import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { useTranslation } from '../../../lib/i18n';
import { formatRelativeTime } from '../../../lib/formatters';

type Props = {
  onRefresh: () => void;
  syncing: boolean;
  lastSyncedAt: number | null;
  /** True when the last sync attempt failed completely; blocking red notice. */
  hasError?: boolean;
  /** Non-blocking notice of a PARTIAL failure (accounts that did not sync),
   *  already composed by the page. Null/omitted when it does not apply. */
  partialWarning?: string | null;
  /** Non-blocking "loading your history…" notice while a background backfill
   *  runs, already composed by the page. A SEPARATE channel from
   *  ``partialWarning``: a backfilling account is excluded from sync so it
   *  never appears there — the two are disjoint and may show at once (different
   *  accounts). Null/omitted when no backfill is active. */
  backfillNotice?: string | null;
};

export default function RefreshControl({
  onRefresh,
  syncing,
  lastSyncedAt,
  hasError = false,
  partialWarning = null,
  backfillNotice = null,
}: Props) {
  const { t, lang } = useTranslation();
  // Local UI ticker (impure by necessity) so the "time ago" text advances on
  // its own WITHOUT launching any sync. 30 s is enough: past the first run of
  // seconds the text crosses into minutes.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  const status = hasError
    ? t('common.syncFailed')
    : lastSyncedAt === null
      ? t('common.lastSyncNever')
      : t('common.lastSync', { time: formatRelativeTime(lastSyncedAt, now, lang) });

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={onRefresh}
        disabled={syncing}
        aria-label={t('common.refreshAria')}
        className="inline-flex items-center gap-2 rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <RefreshCw className={`h-3.5 w-3.5 ${syncing ? 'animate-spin' : ''}`} />
        {syncing ? t('common.syncing') : t('common.refresh')}
      </button>
      <span
        className={`text-[12px] ${hasError ? 'text-red-600' : 'text-zinc-400'}`}
        aria-live="polite"
      >
        {status}
      </span>
      {/* Partial-failure notice: amber, non-blocking, below the status mark.
          Red (hasError) takes precedence — never render both at once. */}
      {!hasError && partialWarning ? (
        <span className="text-[12px] text-amber-600" aria-live="polite">
          {partialWarning}
        </span>
      ) : null}
      {/* Backfill progress: blue, informational, its own channel (independent of
          hasError / partialWarning — a background history download is unrelated
          to the manual sync outcome). */}
      {backfillNotice ? <span className="text-[12px] text-blue-600">{backfillNotice}</span> : null}
    </div>
  );
}
