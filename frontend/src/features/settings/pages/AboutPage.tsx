import { useTranslation } from '../../../lib/i18n';

// App version is a frontend constant (no backend endpoint). It lives here, not
// in lib/constants.ts, because AboutPage is its only consumer (lib/CLAUDE.md
// §3.2 requires lib exports to be used in ≥2 places). Help / privacy / terms
// links are intentionally omitted until real pages exist.
const APP_VERSION = '1.0.0';

export default function AboutPage() {
  const { t } = useTranslation();

  return (
    <div className="flex flex-col gap-8 px-8 pt-8 pb-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-[28px] font-bold tracking-tight text-zinc-900">
          {t('settings.about.title')}
        </h1>
        <p className="text-[15px] leading-[1.5] text-zinc-500">{t('settings.about.subtitle')}</p>
      </div>

      <div className="flex items-center gap-3">
        <div className="h-12 w-12 shrink-0 overflow-hidden rounded-xl">
          <img src="/logo.png" alt="MailManager" className="h-full w-full object-cover" />
        </div>
        <div className="flex flex-col">
          <span className="text-base font-semibold text-zinc-900">MailManager</span>
          <span className="text-sm text-zinc-500">
            {t('settings.about.version', { version: APP_VERSION })}
          </span>
        </div>
      </div>
    </div>
  );
}
