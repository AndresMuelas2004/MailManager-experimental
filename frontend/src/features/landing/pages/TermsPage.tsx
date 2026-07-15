import usePublicLang from '../hooks/usePublicLang';
import PublicHeader from '../components/PublicHeader';
import PublicFooter from '../components/PublicFooter';
import TermsContentEn from '../components/TermsContentEn';
import TermsContentEs from '../components/TermsContentEs';

/** Public terms of service at /terms — reachable with or without a session. */
export default function TermsPage() {
  const { lang, setLang } = usePublicLang();

  return (
    <div className="min-h-screen bg-white text-zinc-900">
      <PublicHeader lang={lang} onLangChange={setLang} />
      <main className="mx-auto w-full max-w-3xl px-4 py-12 sm:px-6">
        <article className="prose prose-zinc max-w-none">
          {lang === 'es' ? <TermsContentEs /> : <TermsContentEn />}
        </article>
      </main>
      <PublicFooter lang={lang} />
    </div>
  );
}
