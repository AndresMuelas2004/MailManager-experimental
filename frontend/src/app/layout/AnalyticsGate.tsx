import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';

import CookieBanner from '../../components/common/CookieBanner';
import {
  isAnalyticsConfigured,
  isTrackedPath,
  loadAnalytics,
  readAnalyticsConsent,
  trackPageView,
  writeAnalyticsConsent,
  type AnalyticsConsent,
} from '../../lib/analytics';
import { useTranslation } from '../../lib/i18n';

type Props = { children: ReactNode };

/**
 * Consent gate for web analytics, mounted by RootLayout so it sits inside the
 * router (it needs the current location) and above every route.
 *
 * Nothing is loaded until the visitor accepts: on a null or 'denied' consent no
 * Google script is ever requested and no cookie is set. The banner itself is
 * only offered on the public pages we measure — a signed-in user working in
 * their mailbox is never interrupted by it, because there is nothing to consent
 * to there (in-app routes are not tracked at all; see lib/analytics.ts).
 */
export default function AnalyticsGate({ children }: Props) {
  const { pathname } = useLocation();
  const { t } = useTranslation();
  const [consent, setConsent] = useState<AnalyticsConsent | null>(() => readAnalyticsConsent());

  useEffect(() => {
    if (consent !== 'granted') return;
    loadAnalytics();
    trackPageView(pathname);
  }, [consent, pathname]);

  const accept = useCallback(() => {
    writeAnalyticsConsent('granted');
    setConsent('granted');
  }, []);

  const reject = useCallback(() => {
    writeAnalyticsConsent('denied');
    setConsent('denied');
  }, []);

  const showBanner = isAnalyticsConfigured() && consent === null && isTrackedPath(pathname);

  return (
    <>
      {children}
      {showBanner && (
        <CookieBanner
          ariaLabel={t('cookies.ariaLabel')}
          message={t('cookies.message')}
          acceptLabel={t('cookies.accept')}
          rejectLabel={t('cookies.reject')}
          policyHref="/privacy"
          policyLabel={t('cookies.policyLink')}
          onAccept={accept}
          onReject={reject}
        />
      )}
    </>
  );
}
