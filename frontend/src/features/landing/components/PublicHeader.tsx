import { Link } from 'react-router-dom';

import LangToggle from './LangToggle';
import type { Lang } from '../../../lib/i18n';

type Props = {
  lang: Lang;
  onLangChange: (lang: Lang) => void;
  /** The "Features" anchor only exists on the landing itself. */
  showFeaturesLink?: boolean;
};

const COPY: Record<Lang, { features: string; privacy: string; terms: string; signIn: string }> = {
  en: { features: 'Features', privacy: 'Privacy', terms: 'Terms', signIn: 'Sign in' },
  es: {
    features: 'Características',
    privacy: 'Privacidad',
    terms: 'Términos',
    signIn: 'Iniciar sesión',
  },
};

export default function PublicHeader({ lang, onLangChange, showFeaturesLink = false }: Props) {
  const copy = COPY[lang];
  return (
    <header className="sticky top-0 z-40 border-b border-zinc-200 bg-white/85 backdrop-blur">
      <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
        <Link to="/" className="flex items-center gap-2.5">
          <span className="h-9 w-9 shrink-0 overflow-hidden rounded-[10px]">
            <img src="/logo.png" alt="MISSELA logo" className="h-full w-full object-cover" />
          </span>
          <span className="text-lg font-extrabold tracking-tight text-zinc-900">MISSELA</span>
        </Link>

        <nav className="hidden items-center gap-6 text-sm font-medium text-zinc-600 sm:flex">
          {showFeaturesLink && (
            <a href="#features" className="transition-colors hover:text-zinc-900">
              {copy.features}
            </a>
          )}
          <Link to="/privacy" className="transition-colors hover:text-zinc-900">
            {copy.privacy}
          </Link>
          <Link to="/terms" className="transition-colors hover:text-zinc-900">
            {copy.terms}
          </Link>
        </nav>

        <div className="flex items-center gap-3">
          <LangToggle lang={lang} onChange={onLangChange} />
          <Link
            to="/login"
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-700"
          >
            {copy.signIn}
          </Link>
        </div>
      </div>
    </header>
  );
}
