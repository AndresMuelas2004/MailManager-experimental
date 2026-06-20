import type { RefObject } from 'react';
import { ShieldCheck } from 'lucide-react';
import type { UiError } from '../../../api/client/errors';
import { useTranslation } from '../../../lib/i18n';

type Props = {
  buttonRef: RefObject<HTMLDivElement | null>;
  error: UiError | null;
  loading: boolean;
};

export default function GoogleSignInButton({ buttonRef, error, loading }: Props) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-1 flex-col items-center justify-center bg-[#F8FAFC] px-20">
      <div className="flex w-full max-w-[400px] flex-col items-center gap-8">
        <div className="flex flex-col items-center gap-2.5">
          <h2 className="text-[32px] font-bold tracking-tight text-slate-950">
            {t('login.welcome')}
          </h2>
          <p className="text-center text-base text-slate-500">{t('login.signInPrompt')}</p>
        </div>

        <div className="w-full">
          <div className="overflow-hidden rounded-2xl shadow-lg shadow-blue-600/25">
            <div ref={buttonRef} />
          </div>

          {loading && (
            <p className="mt-4 text-center text-sm text-gray-500">{t('login.signingIn')}</p>
          )}

          {error && <p className="mt-4 text-center text-sm text-red-600">{error.message}</p>}
        </div>

        <div className="flex items-center gap-1.5">
          <ShieldCheck className="h-[15px] w-[15px] text-slate-400" />
          <span className="text-[13px] text-slate-400">{t('login.secureConnection')}</span>
        </div>

        <p className="max-w-[320px] text-center text-xs text-slate-400">{t('login.termsNotice')}</p>
      </div>
    </div>
  );
}
