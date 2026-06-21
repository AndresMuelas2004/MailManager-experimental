import { useCallback, useState } from 'react';
import { PublicClientApplication } from '@azure/msal-browser';
import type { Configuration } from '@azure/msal-browser';

import { useAuth } from '../../../app/providers/AuthContext';
import { toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';

type UseMicrosoftLoginReturn = {
  trigger: () => Promise<void>;
  error: UiError | null;
  loading: boolean;
};

// Lazy module singleton: the MSAL instance (and its initialize() promise) are
// built on the first trigger() call, not at import time. This is what lets the
// integration tests stub VITE_MICROSOFT_CLIENT_ID before the first interaction
// without needing vi.resetModules() — the client id is read inside the getter,
// not once at module evaluation.
let cachedInstance: PublicClientApplication | null = null;
let cachedReady: Promise<void> | null = null;

// Test-only: reset the module singleton between cases. Because the instance is
// cached lazily (above), once any test builds it the cache survives across the
// other tests in the same Vitest worker — which made the "client id absent" case
// order-dependent. The spec's beforeEach calls this so each test starts from a
// clean singleton; production code never imports it.
export function __resetMsalSingletonForTest(): void {
  cachedInstance = null;
  cachedReady = null;
}

function getMsalInstance(): PublicClientApplication | null {
  if (cachedInstance) {
    return cachedInstance;
  }
  const clientId = import.meta.env.VITE_MICROSOFT_CLIENT_ID as string | undefined;
  if (!clientId) {
    return null;
  }
  cachedInstance = new PublicClientApplication({
    auth: {
      clientId,
      authority: 'https://login.microsoftonline.com/common',
      redirectUri: `${window.location.origin}/redirect.html`,
    },
    cache: { cacheLocation: 'memoryStorage' },
  } satisfies Configuration);
  cachedReady = cachedInstance.initialize();
  return cachedInstance;
}

export default function useMicrosoftLogin(): UseMicrosoftLoginReturn {
  const { loginWithMicrosoft } = useAuth();
  const [error, setError] = useState<UiError | null>(null);
  const [loading, setLoading] = useState(false);

  const trigger = useCallback(async () => {
    setError(null);
    const instance = getMsalInstance();
    if (!instance) {
      setError({ message: 'Microsoft Client ID is not configured.' });
      return;
    }
    setLoading(true);
    try {
      // initialize() must resolve before any other MSAL API call (v5 throws
      // uninitialized_public_client_application otherwise). cachedReady is set
      // alongside the instance, so it is non-null here.
      await cachedReady;
      const response = await instance.loginPopup({
        scopes: ['openid', 'profile', 'email'],
        prompt: 'select_account',
      });
      await loginWithMicrosoft(response.idToken);
    } catch (err: unknown) {
      // MSAL surfaces machine-readable codes on `errorCode` (in v5 `message` is
      // a generic doc link). Backend failures arrive here as ApiError and fall
      // through to toUiError, which already translates rate limiting et al.
      const code = (err as { errorCode?: string }).errorCode;
      if (code === 'timed_out' || code === 'user_cancelled') {
        // v5 does not detect the popup close instantly; the bridge times out and
        // rejects with `timed_out`. Treat it as a soft cancellation, not a crash.
        setError({ message: 'Inicio de sesión cancelado.' });
      } else if (code === 'popup_window_error' || code === 'empty_window_error') {
        setError({
          message: 'Permite las ventanas emergentes para iniciar sesión con Microsoft.',
        });
      } else {
        setError(toUiError(err));
      }
    } finally {
      setLoading(false);
    }
  }, [loginWithMicrosoft]);

  return { trigger, error, loading };
}
