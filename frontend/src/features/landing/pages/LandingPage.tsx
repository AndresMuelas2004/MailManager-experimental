import { Navigate } from 'react-router-dom';

import { useAuth } from '../../../app/providers/AuthContext';
import Spinner from '../../../components/common/Spinner';
import usePublicLang from '../hooks/usePublicLang';
import PublicHeader from '../components/PublicHeader';
import PublicFooter from '../components/PublicFooter';
import LandingContent from '../components/LandingContent';

/**
 * Public marketing landing mounted at "/". Anonymous visitors see it;
 * authenticated ones are forwarded to the mailbox gateway at /home — which is
 * what keeps every pre-existing ``navigate('/')`` / ``to="/"`` call site valid
 * without touching it.
 */
export default function LandingPage() {
  const { user, loading } = useAuth();
  const { lang, setLang } = usePublicLang();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }

  if (user) {
    return <Navigate to="/home" replace />;
  }

  return (
    <div className="min-h-screen bg-white text-zinc-900">
      <PublicHeader lang={lang} onLangChange={setLang} showFeaturesLink />
      <LandingContent lang={lang} />
      <PublicFooter lang={lang} />
    </div>
  );
}
