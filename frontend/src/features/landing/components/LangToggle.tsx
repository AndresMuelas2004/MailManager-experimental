import type { Lang } from '../../../lib/i18n';

type Props = {
  lang: Lang;
  onChange: (lang: Lang) => void;
};

const OPTIONS: Lang[] = ['en', 'es'];

export default function LangToggle({ lang, onChange }: Props) {
  return (
    <div
      role="group"
      aria-label={lang === 'es' ? 'Idioma' : 'Language'}
      className="flex items-center rounded-lg border border-zinc-200 bg-white p-0.5"
    >
      {OPTIONS.map((value) => {
        const active = lang === value;
        return (
          <button
            key={value}
            type="button"
            onClick={() => onChange(value)}
            aria-pressed={active}
            className={`rounded-md px-2 py-1 text-xs font-semibold transition-colors ${
              active ? 'bg-blue-600 text-white' : 'text-zinc-500 hover:text-zinc-900'
            }`}
          >
            {value.toUpperCase()}
          </button>
        );
      })}
    </div>
  );
}
