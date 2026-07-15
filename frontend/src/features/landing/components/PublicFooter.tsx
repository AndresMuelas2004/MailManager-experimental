import { Link } from 'react-router-dom';

import type { Lang } from '../../../lib/i18n';

type Props = {
  lang: Lang;
};

const COPY: Record<Lang, { privacy: string; terms: string }> = {
  en: { privacy: 'Privacy Policy', terms: 'Terms of Service' },
  es: { privacy: 'Política de privacidad', terms: 'Términos de servicio' },
};

export default function PublicFooter({ lang }: Props) {
  const copy = COPY[lang];
  return (
    <footer className="border-t border-zinc-200 py-8">
      <div className="mx-auto flex w-full max-w-6xl flex-col items-center justify-between gap-3 px-4 text-sm text-zinc-500 sm:flex-row sm:px-6">
        <span>© 2026 Missela</span>
        <nav className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1">
          <Link to="/privacy" className="transition-colors hover:text-zinc-900">
            {copy.privacy}
          </Link>
          <span aria-hidden="true">·</span>
          <Link to="/terms" className="transition-colors hover:text-zinc-900">
            {copy.terms}
          </Link>
          <span aria-hidden="true">·</span>
          <a href="mailto:support@missela.app" className="transition-colors hover:text-zinc-900">
            support@missela.app
          </a>
        </nav>
      </div>
    </footer>
  );
}
