import type { UiError } from '../../../api/client/errors';
import { useTranslation } from '../../../lib/i18n';

type Props = {
  onClick: () => void;
  loading: boolean;
  error: UiError | null;
  configured: boolean;
};

function MicrosoftLogo() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 21 21"
      className="h-[18px] w-[18px] shrink-0"
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect x="1" y="1" width="9" height="9" fill="#F25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7FBA00" />
      <rect x="1" y="11" width="9" height="9" fill="#00A4EF" />
      <rect x="11" y="11" width="9" height="9" fill="#FFB900" />
    </svg>
  );
}

export default function MicrosoftSignInButton({ onClick, loading, error, configured }: Props) {
  const { t } = useTranslation();
  return (
    <div className="w-full">
      <button
        type="button"
        onClick={onClick}
        disabled={loading || !configured}
        className="flex h-[44px] w-full items-center justify-center gap-3 rounded-full border border-slate-300 bg-white text-[15px] font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <MicrosoftLogo />
        {loading ? t('login.signingIn') : t('login.continueWithMicrosoft')}
      </button>

      {error && <p className="mt-4 text-center text-sm text-red-600">{error.message}</p>}
    </div>
  );
}
