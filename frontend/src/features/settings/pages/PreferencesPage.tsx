import { Check } from 'lucide-react';

import { useTranslation } from '../../../lib/i18n';
import type { Lang } from '../../../lib/i18n';

const LANGUAGES: Array<{ value: Lang; labelKey: string }> = [
  { value: 'es', labelKey: 'settings.language.spanish' },
  { value: 'en', labelKey: 'settings.language.english' },
];

export default function PreferencesPage() {
  const { t, lang, setLang } = useTranslation();

  return (
    <div className="flex flex-col gap-8 px-4 pt-6 pb-6 lg:px-8 lg:pt-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-bold tracking-tight text-zinc-900 lg:text-[28px]">
          {t('settings.language.title')}
        </h1>
        <p className="text-[15px] leading-[1.5] text-zinc-500">{t('settings.language.subtitle')}</p>
      </div>

      <div className="flex max-w-md flex-col gap-2">
        {LANGUAGES.map(({ value, labelKey }) => {
          const active = lang === value;
          return (
            <button
              key={value}
              type="button"
              onClick={() => setLang(value)}
              aria-pressed={active}
              className={`flex items-center justify-between rounded-lg border px-4 py-3 text-sm font-medium transition-colors ${
                active
                  ? 'border-blue-600 bg-blue-50 text-blue-700'
                  : 'border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50'
              }`}
            >
              {t(labelKey)}
              {active && <Check className="h-4 w-4" />}
            </button>
          );
        })}
      </div>
    </div>
  );
}
