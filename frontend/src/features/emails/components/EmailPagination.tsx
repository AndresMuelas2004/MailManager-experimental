import { ChevronLeft, ChevronRight } from 'lucide-react';

import { useTranslation } from '../../../lib/i18n';

type Props = {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  disabled?: boolean;
};

// Build the list of page tokens to render: always the first and last
// page, a window of ±2 around the current page, and an ``'ellipsis'``
// marker wherever a gap is collapsed (e.g. ``1 … 4 [5] 6 … 25``).
type PageToken = number | 'ellipsis-left' | 'ellipsis-right';

function buildPageTokens(current: number, totalPages: number): PageToken[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }
  const tokens: PageToken[] = [1];
  const start = Math.max(2, current - 2);
  const end = Math.min(totalPages - 1, current + 2);
  if (start > 2) tokens.push('ellipsis-left');
  for (let p = start; p <= end; p++) tokens.push(p);
  if (end < totalPages - 1) tokens.push('ellipsis-right');
  tokens.push(totalPages);
  return tokens;
}

// Navigation controls only (prev / page numbers / next). The "from–to de
// total" range that used to sit on the left now lives in EmailTable's
// header bar, so this component is embeddable on the right side of that
// bar with no footer chrome of its own.
export default function EmailPagination({ page, pageSize, total, onPageChange, disabled }: Props) {
  const { t } = useTranslation();
  if (total === 0) return null;

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const prevDisabled = disabled || page <= 1;
  const nextDisabled = disabled || page >= totalPages;

  const tokens = buildPageTokens(page, totalPages);

  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        onClick={() => onPageChange(page - 1)}
        disabled={prevDisabled}
        aria-label={t('pagination.prevAria')}
        className="inline-flex items-center gap-1 rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <ChevronLeft className="h-4 w-4" />
        {t('pagination.previous')}
      </button>

      {tokens.map((token) => {
        if (token === 'ellipsis-left' || token === 'ellipsis-right') {
          return (
            <span key={token} aria-hidden className="px-1.5 text-[13px] text-zinc-400 select-none">
              …
            </span>
          );
        }
        const isCurrent = token === page;
        return (
          <button
            key={token}
            type="button"
            onClick={() => onPageChange(token)}
            disabled={disabled || isCurrent}
            aria-label={t('pagination.pageAria', { page: token })}
            aria-current={isCurrent ? 'page' : undefined}
            className={`min-w-[34px] rounded-md border px-2.5 py-1.5 text-[13px] font-medium transition-colors disabled:cursor-not-allowed ${
              isCurrent
                ? 'border-blue-600 bg-blue-600 text-white'
                : 'border-zinc-300 bg-white text-zinc-700 hover:bg-zinc-50 disabled:opacity-50'
            }`}
          >
            {token}
          </button>
        );
      })}

      <button
        type="button"
        onClick={() => onPageChange(page + 1)}
        disabled={nextDisabled}
        aria-label={t('pagination.nextAria')}
        className="inline-flex items-center gap-1 rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {t('pagination.next')}
        <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}
