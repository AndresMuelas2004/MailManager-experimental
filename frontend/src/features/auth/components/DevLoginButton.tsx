import { FlaskConical } from 'lucide-react';

import type { UiError } from '../../../api/client/errors';

type Props = {
  onClick: () => void;
  loading: boolean;
  error: UiError | null;
};

export default function DevLoginButton({ onClick, loading, error }: Props) {
  return (
    <div className="fixed right-4 top-4 z-50 flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={onClick}
        disabled={loading}
        aria-label="Dev login"
        className="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-100 px-3 py-2 text-sm font-medium text-amber-900 shadow-sm transition hover:bg-amber-200 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <FlaskConical className="h-4 w-4" />
        {loading ? 'Entrando…' : 'Dev login'}
      </button>
      {error && <p className="max-w-xs text-right text-xs text-red-600">{error.message}</p>}
    </div>
  );
}
