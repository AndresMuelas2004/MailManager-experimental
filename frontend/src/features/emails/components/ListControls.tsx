import { ArrowDown, ArrowUp } from 'lucide-react';

import { useTranslation } from '../../../lib/i18n';
import type { ListControlsState, SortKey } from '../../../lib/listControls';

type Props = {
  value: ListControlsState;
  onChange: (next: ListControlsState) => void;
};

const SORT_OPTIONS: ReadonlyArray<{ value: SortKey; labelKey: string }> = [
  { value: 'date', labelKey: 'listControls.sortDate' },
  { value: 'sender', labelKey: 'listControls.sortSender' },
  { value: 'subject', labelKey: 'listControls.sortSubject' },
];

const CHIP_BASE =
  'rounded-full border px-3 py-1 text-[13px] font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600';
const CHIP_ACTIVE = 'border-blue-600 bg-blue-50 text-blue-700';
const CHIP_INACTIVE = 'border-zinc-200 bg-white text-zinc-600 hover:bg-zinc-50';

export default function ListControls({ value, onChange }: Props) {
  const { t } = useTranslation();
  const dirLabel = value.dir === 'asc' ? t('listControls.dirAsc') : t('listControls.dirDesc');

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-1.5 text-[13px] text-zinc-600">
        <span className="font-medium">{t('listControls.sortLabel')}</span>
        <select
          value={value.sort}
          onChange={(e) => onChange({ ...value, sort: e.target.value as SortKey })}
          className="rounded-md border border-zinc-300 px-2 py-1.5 text-sm"
        >
          {SORT_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {t(opt.labelKey)}
            </option>
          ))}
        </select>
      </label>

      <button
        type="button"
        onClick={() => onChange({ ...value, dir: value.dir === 'asc' ? 'desc' : 'asc' })}
        aria-label={dirLabel}
        title={dirLabel}
        className="grid h-9 w-9 place-items-center rounded-md border border-zinc-300 text-zinc-600 hover:bg-zinc-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
      >
        {value.dir === 'asc' ? <ArrowUp className="h-4 w-4" /> : <ArrowDown className="h-4 w-4" />}
      </button>

      <button
        type="button"
        aria-pressed={value.unread}
        onClick={() => onChange({ ...value, unread: !value.unread })}
        className={`${CHIP_BASE} ${value.unread ? CHIP_ACTIVE : CHIP_INACTIVE}`}
      >
        {t('listControls.filterUnread')}
      </button>
      <button
        type="button"
        aria-pressed={value.hasAttachment}
        onClick={() => onChange({ ...value, hasAttachment: !value.hasAttachment })}
        className={`${CHIP_BASE} ${value.hasAttachment ? CHIP_ACTIVE : CHIP_INACTIVE}`}
      >
        {t('listControls.filterAttachment')}
      </button>
      <button
        type="button"
        aria-pressed={value.favorite}
        onClick={() => onChange({ ...value, favorite: !value.favorite })}
        className={`${CHIP_BASE} ${value.favorite ? CHIP_ACTIVE : CHIP_INACTIVE}`}
      >
        {t('listControls.filterFavorite')}
      </button>
    </div>
  );
}
