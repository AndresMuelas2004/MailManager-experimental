import { Search, X } from 'lucide-react';

import { useTranslation } from '../../../lib/i18n';

// Mirrors the backend ``q`` cap (the listing routers validate
// ``max_length=200``). Enforced on the field so typing/pasting can never
// produce the 422 that would replace the table with a raw error; pages that
// seed the field from the URL clamp to the same value.
export const MAX_SEARCH_LENGTH = 200;

type Props = {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
};

export default function SearchInput({ value, onChange, placeholder }: Props) {
  const { t } = useTranslation();
  return (
    <div className="relative w-full max-w-md">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        maxLength={MAX_SEARCH_LENGTH}
        placeholder={placeholder ?? t('search.placeholder')}
        className="h-10 w-full rounded-lg border border-zinc-200 bg-white pl-9 pr-9 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-blue-600 focus:outline-none"
      />
      {value.length > 0 && (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label={t('search.clear')}
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600"
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
