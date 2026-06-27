import { useCallback, useEffect, useRef, useState } from 'react';

import { useAuth } from '../../../app/providers/AuthContext';
import { toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';

type UseGoogleLoginReturn = {
  buttonRef: React.RefObject<HTMLDivElement | null>;
  error: UiError | null;
  loading: boolean;
};

export default function useGoogleLogin(): UseGoogleLoginReturn {
  const { login } = useAuth();
  const buttonRef = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<UiError | null>(null);
  const [loading, setLoading] = useState(false);

  const handleCredentialResponse = useCallback(
    async (response: google.accounts.id.CredentialResponse) => {
      setError(null);
      setLoading(true);
      try {
        await login(response.credential);
      } catch (err) {
        setError(toUiError(err));
      } finally {
        setLoading(false);
      }
    },
    [login],
  );

  useEffect(() => {
    const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined;
    if (!clientId) {
      setError({ message: 'Google Client ID is not configured.' });
      return;
    }

    let ready = false;
    let resizeTimer: ReturnType<typeof setTimeout> | undefined;

    const renderButton = () => {
      if (!buttonRef.current) return;
      // The GSI widget is sized in fixed pixels (max 400); fit it to the
      // available column width so it never overflows a narrow viewport.
      const width = Math.min(400, buttonRef.current.offsetWidth || 400);
      buttonRef.current.innerHTML = '';
      google.accounts.id.renderButton(buttonRef.current, {
        theme: 'filled_blue',
        size: 'large',
        shape: 'pill',
        text: 'continue_with',
        width,
      });
    };

    const intervalId = setInterval(() => {
      if (typeof google !== 'undefined' && google.accounts?.id && buttonRef.current) {
        clearInterval(intervalId);

        google.accounts.id.initialize({
          client_id: clientId,
          callback: handleCredentialResponse,
        });

        renderButton();
        ready = true;
      }
    }, 100);

    const handleResize = () => {
      if (!ready) return;
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(renderButton, 150);
    };
    window.addEventListener('resize', handleResize);

    return () => {
      clearInterval(intervalId);
      clearTimeout(resizeTimer);
      window.removeEventListener('resize', handleResize);
    };
  }, [handleCredentialResponse]);

  return { buttonRef, error, loading };
}
